// Metadata only: never starts a collector, loads a candidate module, or attaches
// to a process. Build with the Windows SDK (C++17); an external supervisor must
// enforce a wall-clock timeout. No paths, certificate names, or OS error text
// are emitted. 'cached-valid' is NOT a fresh online trust/revocation assertion.
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <shlobj.h>
#include <wintrust.h>
#include <softpub.h>
#include <wincrypt.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <cwchar>
#include <new>
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "version.lib")
#pragma comment(lib, "wintrust.lib")
#pragma comment(lib, "crypt32.lib")

namespace {
void emit(const char* stage, const char* status, const char* value = "null") {
    std::printf("{\"stage\":\"%s\",\"status\":\"%s\",\"value\":%s}\n", stage, status, value);
    std::fflush(stdout);
}
void start(const char* stage) { emit(stage, "started"); }
struct Handles {
    std::vector<HANDLE> items;
    ~Handles() { for (HANDLE h : items) CloseHandle(h); }
};
struct Trust {
    WINTRUST_FILE_INFO file{};
    WINTRUST_DATA data{};
    GUID action = WINTRUST_ACTION_GENERIC_VERIFY_V2;
    bool called = false;
    bool closed = false;
    bool close() {
        if (!called || closed) return true;
        closed = true;
        start("trust-close");
        data.dwStateAction = WTD_STATEACTION_CLOSE;
        const LONG result = WinVerifyTrust(nullptr, &action, &data);
        emit("trust-close", result == ERROR_SUCCESS ? "ok" : "probeerror",
             result == ERROR_SUCCESS ? "true" : "null");
        return result == ERROR_SUCCESS;
    }
    ~Trust() { close(); }
};
int finish(Trust& trust, const char* status, int code, bool verified = false) {
    if (!trust.close()) { status = "probeerror"; code = 4; verified = false; }
    start("complete");
    emit("complete", status, verified ? "true" : "false");
    return code;
}
bool readAt(HANDLE h, LONGLONG offset, void* buffer, DWORD size) {
    LARGE_INTEGER pos{}; pos.QuadPart = offset;
    DWORD read = 0;
    return SetFilePointerEx(h, pos, nullptr, FILE_BEGIN) &&
        ReadFile(h, buffer, size, &read, nullptr) && read == size;
}
bool missing(DWORD error) {
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND;
}
// Hold every ancestor against replacement; never follow a reparse point.
// The Windows system roots must be local drive-absolute paths.
const char* openFixed(const std::wstring& path, Handles& handles) {
    if (path.size() < 4 || path[1] != L':' || path[2] != L'\\' ||
        !((path[0] >= L'A' && path[0] <= L'Z') || (path[0] >= L'a' && path[0] <= L'z')))
        return "refused";
    size_t end = 3;
    for (;;) {
        const bool last = end == path.size();
        const std::wstring prefix = path.substr(0, end);
        HANDLE h = CreateFileW(prefix.c_str(), last ? GENERIC_READ : FILE_READ_ATTRIBUTES,
            FILE_SHARE_READ, nullptr, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS, nullptr);
        if (h == INVALID_HANDLE_VALUE) return missing(GetLastError()) ? "missing" : "probeerror";
        handles.items.push_back(h);
        BY_HANDLE_FILE_INFORMATION info{};
        if (!GetFileInformationByHandle(h, &info)) return "probeerror";
        if ((info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
            (last ? (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0 :
                    (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) == 0)) return "refused";
        if (last) return "ok";
        end = path.find(L'\\', end == 3 ? 3 : end + 1);
        if (end == std::wstring::npos) end = path.size();
    }
}
int run(int argc, char** argv) {
    // Keep paths and file handles alive until trust state is closed, even when
    // an allocation throws and the stack unwinds.
    std::wstring path;
    Handles handles;
    Trust trust;
    start("exists");
    const wchar_t* suffix = nullptr;
    bool cdb = false;
    if (argc == 2) {
        if (!std::strcmp(argv[1], "cdb")) { cdb = true; suffix = L"\\Windows Kits\\10\\Debuggers\\x64\\cdb.exe"; }
        else if (!std::strcmp(argv[1], "clr")) suffix = L"\\Microsoft.NET\\Framework64\\v4.0.30319\\clr.dll";
        else if (!std::strcmp(argv[1], "sos")) suffix = L"\\Microsoft.NET\\Framework64\\v4.0.30319\\sos.dll";
        else if (!std::strcmp(argv[1], "mscordacwks")) suffix = L"\\Microsoft.NET\\Framework64\\v4.0.30319\\mscordacwks.dll";
    }
    if (!suffix) { emit("exists", "refused"); return finish(trust, "refused", 3); }
    if (cdb) {
        PWSTR folder = nullptr;
        const HRESULT hr = SHGetKnownFolderPath(FOLDERID_ProgramFilesX86, KF_FLAG_DONT_VERIFY, nullptr, &folder);
        if (FAILED(hr) || !folder) {
            if (folder) CoTaskMemFree(folder);
            emit("exists", "probeerror"); return finish(trust, "probeerror", 4);
        }
        path = folder; CoTaskMemFree(folder);
    } else {
        wchar_t root[MAX_PATH + 1]{};
        const UINT size = GetWindowsDirectoryW(root, MAX_PATH + 1);
        if (!size || size > MAX_PATH) { emit("exists", "probeerror"); return finish(trust, "probeerror", 4); }
        path = root;
    }
    while (!path.empty() && path.back() == L'\\') path.pop_back();
    path += suffix;
    const char* opened = openFixed(path, handles);
    if (std::strcmp(opened, "ok")) {
        emit("exists", opened, !std::strcmp(opened, "missing") ? "false" : "null");
        return finish(trust, opened, !std::strcmp(opened, "missing") ? 2 : !std::strcmp(opened, "refused") ? 3 : 4);
    }
    emit("exists", "ok", "true");
    HANDLE h = handles.items.back();
    start("pe");
    LARGE_INTEGER length{};
    IMAGE_DOS_HEADER dos{};
    DWORD signature = 0;
    IMAGE_FILE_HEADER header{};
    if (!GetFileSizeEx(h, &length) || !readAt(h, 0, &dos, sizeof(dos))) {
        emit("pe", "probeerror"); return finish(trust, "probeerror", 4);
    }
    if (dos.e_magic != IMAGE_DOS_SIGNATURE || dos.e_lfanew < static_cast<LONG>(sizeof(dos)) ||
        dos.e_lfanew > 1024 * 1024 || length.QuadPart < static_cast<LONGLONG>(dos.e_lfanew) + sizeof(signature) + sizeof(header)) {
        emit("pe", "refused"); return finish(trust, "refused", 3);
    }
    if (!readAt(h, dos.e_lfanew, &signature, sizeof(signature)) ||
        !readAt(h, static_cast<LONGLONG>(dos.e_lfanew) + sizeof(signature), &header, sizeof(header))) {
        emit("pe", "probeerror"); return finish(trust, "probeerror", 4);
    }
    if (signature != IMAGE_NT_SIGNATURE) { emit("pe", "refused"); return finish(trust, "refused", 3); }
    const char* arch = header.Machine == IMAGE_FILE_MACHINE_AMD64 ? "\"x64\"" :
        header.Machine == IMAGE_FILE_MACHINE_I386 ? "\"x86\"" :
        header.Machine == IMAGE_FILE_MACHINE_ARM64 ? "\"arm64\"" : "\"other\"";
    const bool x64 = header.Machine == IMAGE_FILE_MACHINE_AMD64;
    emit("pe", "ok", arch);
    start("version");
    DWORD unused = 0;
    const DWORD size = GetFileVersionInfoSizeW(path.c_str(), &unused);
    if (!size || size > 1024 * 1024) { emit("version", "probeerror"); return finish(trust, "probeerror", 4); }
    std::vector<BYTE> bytes(size);
    VS_FIXEDFILEINFO* version = nullptr;
    UINT versionSize = 0;
    if (!GetFileVersionInfoW(path.c_str(), 0, size, bytes.data()) ||
        !VerQueryValueW(bytes.data(), L"\\", reinterpret_cast<void**>(&version), &versionSize) ||
        !version || versionSize < sizeof(*version) || version->dwSignature != 0xFEEF04BD) {
        emit("version", "probeerror"); return finish(trust, "probeerror", 4);
    }
    char numeric[64]{};
    std::snprintf(numeric, sizeof(numeric), "[%u,%u,%u,%u]",
        static_cast<unsigned>(HIWORD(version->dwFileVersionMS)), static_cast<unsigned>(LOWORD(version->dwFileVersionMS)),
        static_cast<unsigned>(HIWORD(version->dwFileVersionLS)), static_cast<unsigned>(LOWORD(version->dwFileVersionLS)));
    emit("version", "ok", numeric);
    start("signature");
    trust.file.cbStruct = sizeof(trust.file);
    trust.file.pcwszFilePath = path.c_str();
    trust.file.hFile = h;
    trust.data.cbStruct = sizeof(trust.data);
    trust.data.dwUIChoice = WTD_UI_NONE;
    trust.data.fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN;
    trust.data.dwUnionChoice = WTD_CHOICE_FILE;
    trust.data.pFile = &trust.file;
    trust.data.dwStateAction = WTD_STATEACTION_VERIFY;
    trust.data.dwProvFlags = WTD_CACHE_ONLY_URL_RETRIEVAL | WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT;
    trust.called = true;
    const LONG result = WinVerifyTrust(nullptr, &trust.action, &trust.data);
    if (result != ERROR_SUCCESS) {
        const char* status = "refused";
        const char* value = "\"untrusted\"";
        int code = 3;
        if (result == CRYPT_E_REVOCATION_OFFLINE || result == CRYPT_E_NO_REVOCATION_CHECK ||
            result == CERT_E_REVOCATION_FAILURE) { status = "unknown"; value = "\"cache-unavailable\""; code = 4; }
        else if (result == TRUST_E_NOSIGNATURE) value = "\"unsigned\"";
        else if (result == TRUST_E_BAD_DIGEST) value = "\"bad-signature\"";
        else if (result == CERT_E_REVOKED || result == CRYPT_E_REVOKED) value = "\"revoked\"";
        else if (result != TRUST_E_SUBJECT_NOT_TRUSTED && result != CERT_E_UNTRUSTEDROOT &&
                 result != CERT_E_CHAINING && result != CERT_E_EXPIRED && result != CERT_E_WRONG_USAGE &&
                 result != TRUST_E_EXPLICIT_DISTRUST) { status = "probeerror"; value = "\"error\""; code = 4; }
        emit("signature", status, value);
        return finish(trust, status, code);
    }
    emit("signature", "ok", "\"cached-valid\"");
    start("publisher");
    CRYPT_PROVIDER_DATA* provider = WTHelperProvDataFromStateData(trust.data.hWVTStateData);
    CRYPT_PROVIDER_SGNR* signer = provider ? WTHelperGetProvSignerFromChain(provider, 0, FALSE, 0) : nullptr;
    if (!signer || !signer->csCertChain || !signer->pasCertChain || !signer->pasCertChain[0].pCert) {
        emit("publisher", "probeerror"); return finish(trust, "probeerror", 4);
    }
    wchar_t organization[128]{};
    const DWORD chars = CertGetNameStringW(signer->pasCertChain[0].pCert, CERT_NAME_ATTR_TYPE, 0,
        const_cast<char*>(szOID_ORGANIZATION_NAME), organization, 128);
    if (chars <= 1 || chars > 128) { emit("publisher", "probeerror"); return finish(trust, "probeerror", 4); }
    const bool microsoft = std::wcscmp(organization, L"Microsoft Corporation") == 0;
    emit("publisher", microsoft ? "ok" : "refused", microsoft ? "true" : "false");
    return finish(trust, microsoft && x64 ? "ok" : "refused", microsoft && x64 ? 0 : 3, microsoft && x64);
}
} // namespace
int main(int argc, char** argv) {
    try { return run(argc, argv); }
    catch (...) {
        start("complete"); emit("complete", "probeerror", "false"); return 4;
    }
}
