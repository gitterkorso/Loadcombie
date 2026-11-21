/* GUI launcher for the relocatable, application-local Windows distribution. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <strsafe.h>
#include <wchar.h>

#define CAPACITY 32768
static WCHAR executable[CAPACITY];
static WCHAR command[CAPACITY];

static int fail(const WCHAR *message)
{
    MessageBoxW(NULL, message, L"Lastkomb", MB_OK | MB_ICONERROR);
    return 1;
}

int WINAPI wWinMain(HINSTANCE current, HINSTANCE previous, LPWSTR arguments, int show)
{
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION child = {0};
    DWORD status = 1;
    DWORD size = GetModuleFileNameW(NULL, executable, CAPACITY);
    if (size == 0 || size >= CAPACITY)
        return fail(L"Sökvägen till Lastkomb kunde inte läsas.");

    WCHAR *separator = wcsrchr(executable, L'\\');
    if (!separator)
        return fail(L"Programmets mapp kunde inte hittas.");
    separator[1] = L'\0';
    if (FAILED(StringCchCatW(executable, CAPACITY, L"runtime\\pythonw.exe")) ||
        FAILED(StringCchPrintfW(command, CAPACITY, L"\"%ls\" -B -m lastkomb %ls", executable, arguments)))
        return fail(L"Sökvägen eller argumenten är för långa.");

    startup.cb = sizeof(startup);
    if (!CreateProcessW(executable, command, NULL, NULL, FALSE, 0, NULL, NULL, &startup, &child))
        return fail(L"Python kunde inte startas. Packa upp hela ZIP-filen och behåll mapparna bredvid Lastkomb.exe.");

    CloseHandle(child.hThread);
    WaitForSingleObject(child.hProcess, INFINITE);
    GetExitCodeProcess(child.hProcess, &status);
    CloseHandle(child.hProcess);
    if (status != 0)
        return fail(L"Programmet avslutades med ett fel. Öppna en terminal i programmets mapp och kör runtime\\python.exe -m lastkomb för att se felmeddelandet.");
    return 0;
}
