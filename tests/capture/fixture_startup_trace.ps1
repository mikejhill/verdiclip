# Opt-in, bounded, private diagnostic sidecar. No GUI, compiler or ETW session here.
function Initialize-FixtureTrace([string]$Directory, [string]$Nonce) {
    $script:fixtureTraceEnabled = $false
    $script:fixtureTraceWriter = $null
    try {
        if ($PSVersionTable.PSVersion.Major -ne 5 -or $PSVersionTable.PSEdition -cne 'Desktop' -or
            [Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
            [Threading.Thread]::CurrentThread.ApartmentState -ne [Threading.ApartmentState]::STA -or
            -not [Diagnostics.Stopwatch]::IsHighResolution) { return }
        if ($Nonce -cnotmatch '\A[a-f0-9]{32}\z') { return }
        $path = [IO.Path]::GetFullPath($Directory)
        $root = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\', '/')
        $info = [IO.DirectoryInfo]::new($path)
        if (-not $info.Exists -or $info.Parent.FullName.TrimEnd('\', '/') -cne $root -or
            $info.Name -cnotmatch '\AVerdiClip-[a-f0-9]{32}\z' -or
            ($info.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
            ($info.Parent.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { return }
        $stream = [IO.FileStream]::new((Join-Path $path 'markers.csv'), [IO.FileMode]::CreateNew,
            [IO.FileAccess]::Write, [IO.FileShare]::Read)
        try { $script:fixtureTraceWriter = [IO.StreamWriter]::new($stream, [Text.Encoding]::ASCII) }
        catch { $stream.Dispose(); throw }
        $script:fixtureTraceWriter.AutoFlush = $true
        $script:fixtureTraceNonce = $Nonce
        $script:fixtureTraceCount = 0
        $script:fixtureTraceSeen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
        $script:fixtureTraceEnabled = $true
    } catch { Close-FixtureTrace } # Optional diagnostics never replace native behavior.
}
function Write-FixtureTrace([string]$Name) {
    if (-not $script:fixtureTraceEnabled) { return }
    try {
        # Closed names only; no UI content, paths, arbitrary strings or absolute UTC.
        if ($Name -cnotin @('entry', 'assemblies_begin', 'assemblies_end', 'identity_begin',
            'identity_end', 'window_begin', 'window_end', 'controls_begin', 'controls_end',
            'pixels_begin', 'pixels_end', 'bitmap_begin', 'bitmap_end', 'freeze_begin',
            'freeze_end', 'content_begin', 'content_end', 'show_begin', 'render',
            'activation_begin', 'activation_end', 'snapshot_begin', 'snapshot_end',
            'stop_observed', 'retirement_begin', 'fixture_retired')) { throw 'Invalid trace name' }
        if ($script:fixtureTraceSeen.Contains($Name)) { return }
        if ($script:fixtureTraceCount -ge 26) { throw 'Trace budget exceeded' }
        # Full .NET Framework returns the native Windows thread identifier. It is
        # sampled at EVERY marker and later checked against one exact ETW lifetime.
        $thread = [AppDomain]::GetCurrentThreadId()
        $tick = [Diagnostics.Stopwatch]::GetTimestamp()
        $frequency = [Diagnostics.Stopwatch]::Frequency
        if ($thread -le 0 -or $tick -le 0 -or $frequency -le 0) { throw 'Invalid trace clock' }
        $culture = [Globalization.CultureInfo]::InvariantCulture
        $script:fixtureTraceCount++
        $line = $script:fixtureTraceCount.ToString($culture) + ',' + $Name + ',' +
            $PID.ToString($culture) + ',' + $thread.ToString($culture) + ',' +
            $tick.ToString($culture) + ',' + $frequency.ToString($culture) + ',' + $script:fixtureTraceNonce
        $script:fixtureTraceWriter.WriteLine($line)
        $null = $script:fixtureTraceSeen.Add($Name)
    } catch { Close-FixtureTrace }
}
function Close-FixtureTrace {
    $script:fixtureTraceEnabled = $false
    if ($null -ne $script:fixtureTraceWriter) {
        try { $script:fixtureTraceWriter.Dispose() } catch { }
        $script:fixtureTraceWriter = $null
    }
}
