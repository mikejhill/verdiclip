'use strict';

const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const REPOSITORY = 'mikejhill/verdiclip';
const POLICY = 'verdiclip-mutmut-3.8-full-v1';
const ASSIGNEE = 'mikejhill';
const DETECTED = ['killed', 'timeout', 'caught by type check', 'segfault'];
const UNDETECTED = ['survived', 'no tests', 'suspicious'];
const integer = value => Number.isSafeInteger(value) && value >= 0;
const marker = kind => `<!-- verdiclip-mutation:${kind}:v1 -->`;

function strictJSON(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text, 'utf8') > 1048576) throw new Error('JSON size limit');
  let cursor = 0;
  const space = () => { while (/\s/u.test(text[cursor] || '') && cursor < text.length) cursor++; };
  function string() {
    const start = cursor++;
    while (cursor < text.length) {
      const character = text[cursor++];
      if (character === '\\') cursor++;
      else if (character === '"') return JSON.parse(text.slice(start, cursor));
    }
    throw new Error('Unterminated JSON string');
  }
  function value(depth) {
    if (depth > 32) throw new Error('JSON depth limit');
    space();
    if (text[cursor] === '{') {
      cursor++; space(); const keys = new Set();
      if (text[cursor] === '}') { cursor++; return; }
      while (cursor < text.length) {
        space(); if (text[cursor] !== '"') throw new Error('JSON object key');
        const key = string(); if (keys.has(key)) throw new Error('Duplicate JSON key'); keys.add(key);
        space(); if (text[cursor++] !== ':') throw new Error('JSON colon'); value(depth + 1); space();
        const separator = text[cursor++]; if (separator === '}') return;
        if (separator !== ',') throw new Error('JSON separator');
      }
    } else if (text[cursor] === '[') {
      cursor++; space(); if (text[cursor] === ']') { cursor++; return; }
      while (cursor < text.length) { value(depth + 1); space(); const separator = text[cursor++];
        if (separator === ']') return; if (separator !== ',') throw new Error('JSON array separator'); }
    } else if (text[cursor] === '"') string();
    else {
      const start = cursor;
      while (cursor < text.length && !/[\s,\]}]/u.test(text[cursor])) cursor++;
      if (cursor === start) throw new Error('Missing JSON value');
    }
  }
  value(0); space(); if (cursor !== text.length) throw new Error('Trailing JSON');
  return JSON.parse(text);
}

function classify(report, run, expectedScoreFailure = false) {
  const failure = reason => ({status: 'incomplete', complete: false, reason, report: null});
  if (!report || report.schema !== 1 || report.repository !== REPOSITORY || report.source_sha !== run.head_sha ||
      report.run_id !== run.id || report.run_attempt !== run.run_attempt || report.policy !== POLICY || report.scope !== 'full') {
    return failure('Missing, malformed, stale or mismatched structured evidence');
  }
  if (!['healthy', 'findings', 'incomplete'].includes(report.status) || typeof report.complete !== 'boolean') {
    return failure('Unsupported result state');
  }
  if (report.status === 'incomplete' || !report.complete) return failure(`Execution incomplete (${run.conclusion})`);
  const counts = report.counts;
  const statuses = [...DETECTED, ...UNDETECTED];
  if (!counts || Array.isArray(counts) || Object.keys(counts).some(key => !statuses.includes(key)) ||
      Object.values(counts).some(value => !integer(value)) || !integer(report.total) || !report.total ||
      !integer(report.detected) || !integer(report.undetected) || !integer(report.historical_verdicts) ||
      report.historical_verdicts > report.total || typeof report.fresh !== 'boolean' ||
      report.fresh !== (report.historical_verdicts === 0)) return failure('Invalid verdict accounting');
  const detected = DETECTED.reduce((sum, key) => sum + (counts[key] || 0), 0);
  const missed = UNDETECTED.reduce((sum, key) => sum + (counts[key] || 0), 0);
  const score = 100 * detected / report.total;
  if (detected !== report.detected || missed !== report.undetected || detected + missed !== report.total ||
      !Number.isFinite(report.score) || Math.abs(score - report.score) > 1e-8 ||
      !Number.isFinite(report.threshold) || report.threshold < 0 || report.threshold > 100 ||
      Number(report.threshold.toFixed(6)) !== report.threshold ||
      !report.outcomes || ['BASELINE_OUTCOME', 'PROBE_OUTCOME', 'MUTATION_OUTCOME', 'IDENTITY_OUTCOME']
        .some(key => report.outcomes[key] !== 'success')) return failure('Completion/score disagrees with raw evidence');
  const key = crypto.createHash('sha256').update(`${POLICY}\nfull\n${report.threshold.toFixed(6)}`).digest('hex');
  if (report.comparable_key !== key) return failure('Incomparable policy identity');
  const findings = score < report.threshold;
  if (report.status !== (findings ? 'findings' : 'healthy')) return failure('Declared status disagrees with verdicts');
  if (run.conclusion !== 'success' && !(run.conclusion === 'failure' && score < report.threshold && expectedScoreFailure)) {
    return failure(`Workflow did not complete successfully (${run.conclusion})`);
  }
  return {status: findings ? 'findings' : 'healthy', complete: true, reason: findings ?
    'Mutation score is below the configured threshold' : 'Complete score meets the configured threshold', report};
}

function parseState(issue, kind) {
  if (!issue) return {version: 1, kind, watermark: null, pending: {}};
  const match = /<!-- verdiclip-mutation-state:(.+?) -->/u.exec(issue.body || '');
  if (!match) throw new Error('Managed issue state missing; preserve it for manual reconciliation');
  const state = strictJSON(match[1]);
  if (state.version !== 1 || state.kind !== kind || !state.pending || typeof state.pending !== 'object' || Array.isArray(state.pending) ||
      Object.keys(state.pending).length > 64 || Object.keys(state.pending).some(key => !/^[a-f0-9]{64}$/u.test(key))) {
    throw new Error('Malformed managed issue state; no overwrite');
  }
  if (Object.values(state.pending).some(item => !item || item.policy !== POLICY || item.scope !== 'full' ||
      !Number.isFinite(item.threshold) || item.threshold < 0 || item.threshold > 100)) throw new Error('Malformed outstanding policy');
  if (state.watermark !== null && (!state.watermark || typeof state.watermark !== 'object' ||
      !integer(state.watermark.run_id) || !state.watermark.run_id || !integer(state.watermark.attempt) || !state.watermark.attempt ||
      !integer(state.watermark.run_number) || !state.watermark.run_number || !Number.isFinite(Date.parse(state.watermark.created_at)))) {
    throw new Error('Malformed evidence watermark');
  }
  return state;
}

function newer(run, old) {
  if (!old) return true;
  if (run.id === old.run_id) return run.run_attempt > old.attempt;
  const time = Date.parse(run.created_at) - Date.parse(old.created_at);
  if (time) return time > 0;
  if (run.run_number !== old.run_number) return run.run_number > old.run_number;
  return run.id > old.run_id;
}

function render(kind, state, outcome, run, active) {
  const report = outcome.report;
  const pending = Object.values(state.pending).map(item => `- ${item.policy}; ${item.scope}; floor ${item.threshold}%`).join('\n');
  const evidence = report ?
    `Scope: **full**; ${report.total} mutants, ${report.detected} detected, ${report.undetected} undetected.\n` +
    `Verdicts: ${Object.entries(report.counts).map(([name, count]) => `${count} ${name}`).join(', ')}.\n` +
    `Score: ${report.score.toFixed(2)}%; floor: ${report.threshold}%. Fresh: ${report.fresh}; historical verdicts: ${report.historical_verdicts}.\n` :
    'Score/completion not established; partial output is not a passing result.\n';
  return `${marker(kind)}\n<!-- verdiclip-mutation-state:${JSON.stringify(state)} -->\n\n` +
    `## Mutation ${kind}: ${active ? 'ACTION REQUIRED' : 'RECOVERED'}\n\nOwner: @${ASSIGNEE}\n` +
    `Evidence: [run ${run.run_number}, attempt ${run.run_attempt}](${run.html_url})\nCommit: \`${run.head_sha}\`\n` +
    `Outcome: **${outcome.status}**; workflow conclusion: **${run.conclusion}**. ${outcome.reason}.\n\n${evidence}\n` +
    `Download \`mutation-results-${run.run_attempt}\` for logs, survivors and identity receipts.\n\n` +
    (kind === 'findings' ?
      `Review survivors/no-tests/suspicious and crash/timeout verdicts; add meaningful assertions or record reviewed equivalence. ` +
      `Never lower the floor to hide findings. Only a newer complete **fresh comparable** default-branch result clears its policy tracker.\n\nOutstanding policies:\n${pending || '- none'}\n` :
      `Inspect baseline, identity, setup and runner logs, repair the execution and rerun on the default branch. ` +
      `An execution recovery records current service health, not proof of a flaky failure's root cause; preserve failing evidence and investigate recurrence.\n`) +
    '\nTriage within one working day and review open trackers weekly. Mutation is asynchronous: it is not a PR, merge or release gate. ' +
    'The issue body is managed; keep investigation notes in comments. See [mutation triage](https://github.com/mikejhill/verdiclip/blob/main/docs/mutation-testing.md).\n';
}

async function listIssues(github, repo) {
  const all = [];
  for (let page = 1; page <= 100; page++) {
    const response = await github.rest.issues.listForRepo({...repo, state: 'all', per_page: 100, page});
    if (!Array.isArray(response.data)) throw new Error('Malformed issue page');
    all.push(...response.data.filter(issue => !issue.pull_request));
    if (response.data.length < 100) return all;
  }
  throw new Error('Issue pagination bound; preserve existing trackers');
}

async function publish({github, context, core, report: supplied}) {
  const run = context.payload.workflow_run;
  const repository = context.payload.repository;
  if (repository.full_name !== REPOSITORY || run.head_repository?.full_name !== REPOSITORY ||
      run.head_branch !== repository.default_branch || run.status !== 'completed' ||
      !['push', 'schedule', 'workflow_dispatch'].includes(run.event)) return;
  if (!integer(run.id) || !run.id || !integer(run.workflow_id) || !run.workflow_id || !integer(run.run_attempt) || !run.run_attempt || !integer(run.run_number) || !run.run_number ||
      !/^[a-f0-9]{40}$/u.test(run.head_sha) || !Number.isFinite(Date.parse(run.created_at)) ||
      run.html_url !== `https://github.com/${REPOSITORY}/actions/runs/${run.id}`) throw new Error('Invalid trusted run identity');
  const repo = {owner: ASSIGNEE, repo: 'verdiclip'};
  // A delayed completion event must not publish an obsolete rerun attempt.
  const live = (await github.rest.actions.getWorkflowRun({...repo, run_id: run.id})).data;
  if (live.run_attempt !== run.run_attempt || live.status !== 'completed' || live.head_sha !== run.head_sha || live.conclusion !== run.conclusion) return;
  // Healthy-first runs need an ordering fence without creating a healthy issue.
  // Ask trusted Actions metadata, never artifact-supplied ordering. In-progress
  // runs do not fence completed evidence. Bound pagination and fail closed.
  for (let page = 1; page <= 10; page++) {
    const history = (await github.rest.actions.listWorkflowRuns({...repo, workflow_id: run.workflow_id,
      branch: repository.default_branch, status: 'completed', created: `>=${run.created_at}`, per_page: 100, page})).data.workflow_runs;
    if (!Array.isArray(history)) throw new Error('Missing trusted workflow history');
    if (history.some(item => item.id !== run.id && item.status === 'completed' &&
        item.head_branch === repository.default_branch && item.head_repository?.full_name === REPOSITORY &&
        ['push', 'schedule', 'workflow_dispatch'].includes(item.event) && newer(item,
          {run_id: run.id, attempt: run.run_attempt, run_number: run.run_number, created_at: run.created_at}))) return;
    if (history.length < 100) break;
    if (page === 10) throw new Error('Workflow history pagination bound; preserve trackers');
  }
  let expectedScoreFailure = false;
  if (run.conclusion === 'failure') {
    const jobs = (await github.rest.actions.listJobsForWorkflowRunAttempt({...repo, run_id: run.id,
      attempt_number: run.run_attempt, per_page: 100})).data;
    if (!Array.isArray(jobs.jobs) || !integer(jobs.total_count) || jobs.total_count !== jobs.jobs.length) throw new Error('Incomplete job evidence');
    const failed = jobs.jobs.flatMap(job => (job.steps || []).filter(step => step.conclusion === 'failure'));
    expectedScoreFailure = jobs.jobs.length > 0 && failed.length === 1 &&
      failed[0].name === 'Report asynchronous score and findings' &&
      jobs.jobs.every(job => ['success', 'failure'].includes(job.conclusion) && Array.isArray(job.steps) &&
        (job.conclusion !== 'failure' || job.steps.some(step => step.conclusion === 'failure' && step.name === 'Report asynchronous score and findings')) &&
        job.steps.every(step => ['success', 'skipped', 'neutral'].includes(step.conclusion) ||
          (step.conclusion === 'failure' && step.name === 'Report asynchronous score and findings')));
  }
  let report = supplied;
  if (report === undefined) {
    try {
      const configured = process.env.MUTATION_EVIDENCE_DIR;
      if (!configured || !path.isAbsolute(configured)) throw new Error('Missing trusted artifact directory');
      const directory = path.resolve(configured);
      const filename = path.join(directory, 'mutation-result.json');
      const dirInfo = fs.lstatSync(directory), fileInfo = fs.lstatSync(filename);
      if (!dirInfo.isDirectory() || dirInfo.isSymbolicLink() || !fileInfo.isFile() || fileInfo.isSymbolicLink() ||
          fileInfo.nlink !== 1 || fileInfo.size > 1048576) throw new Error('Unsafe artifact data');
      report = strictJSON(new TextDecoder('utf-8', {fatal: true}).decode(fs.readFileSync(filename)));
    } catch { report = null; }
  }
  const outcome = classify(report, run, expectedScoreFailure);
  const issues = await listIssues(github, repo);
  const managed = ['findings', 'execution'].map(kind => {
    const candidates = issues.filter(issue => ['github-actions[bot]', ASSIGNEE].includes(issue.user?.login) && (issue.body || '').includes(marker(kind)));
    if (candidates.length > 1) throw new Error('Duplicate managed trackers require manual reconciliation');
    const issue = candidates[0];
    const state = parseState(issue, kind);
    return {kind, issue, state};
  });
  // The execution tracker also fences delayed healthy events after a newer
  // incomplete run. A rerun of the same event may finish a partial API update.
  if (managed.some(({state}) => state.watermark && !newer(run, state.watermark) &&
      !(run.id === state.watermark.run_id && run.run_attempt === state.watermark.attempt))) return;
  for (const {kind, issue, state} of managed) {
    let active;
    if (kind === 'findings') {
      if (outcome.status === 'findings') {
        state.pending[outcome.report.comparable_key] = {policy: POLICY, scope: 'full', threshold: outcome.report.threshold};
        if (Object.keys(state.pending).length > 64) throw new Error('Outstanding policy bound');
      } else if (outcome.status === 'healthy' && outcome.report.fresh) delete state.pending[outcome.report.comparable_key];
      // Partial/skipped/failed runs retain every outstanding policy.
      if (outcome.status === 'incomplete' && !issue) continue;
      active = Object.keys(state.pending).length > 0;
    } else active = outcome.status === 'incomplete';
    if (!issue && !active) continue;
    state.watermark = {run_id: run.id, attempt: run.run_attempt, run_number: run.run_number, created_at: run.created_at};
    const body = render(kind, state, outcome, run, active);
    if (issue) {
      await github.rest.issues.update({...repo, issue_number: issue.number, body, state: active ? 'open' : 'closed',
        state_reason: active ? 'reopened' : 'completed', assignees: [ASSIGNEE]});
    } else await github.rest.issues.create({...repo, title: `Mutation testing: ${kind === 'findings' ? 'findings require triage' : 'execution incomplete or failed'}`, body, assignees: [ASSIGNEE]});
    core.info(`${active ? 'Updated/opened' : 'Recovered'} ${kind} tracker`);
  }
  await core.summary.addHeading('Asynchronous mutation alerts').addRaw(`Run ${run.id}: ${outcome.status}. ${outcome.reason}.`).write();
}

module.exports = {publish, classify, strictJSON, newer, marker};
