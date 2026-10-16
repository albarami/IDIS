[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $FilePath,

    [Parameter(Mandatory = $true)]
    [AllowEmptyCollection()]
    [string[]] $ArgumentList,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $WorkingDirectory,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $StandardOutputPath,

    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string] $StandardErrorPath,

    [switch] $Background
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Resolve-FreshCapturePath {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $FailurePrefix
    )

    $Resolved = [IO.Path]::GetFullPath($Path)
    $Parent = [IO.Path]::GetDirectoryName($Resolved)
    if ([string]::IsNullOrWhiteSpace($Parent) -or -not (Test-Path -LiteralPath $Parent -PathType Container)) {
        throw "${FailurePrefix}_PARENT_MISSING"
    }
    if (Test-Path -LiteralPath $Resolved) {
        throw "${FailurePrefix}_ALREADY_EXISTS"
    }
    return $Resolved
}

function ConvertTo-WindowsNativeArgument {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string] $Argument
    )

    if ($Argument.Length -gt 0 -and $Argument -notmatch '[\s"]') {
        return $Argument
    }

    # Start-Process joins ArgumentList with spaces on Windows PowerShell 5.1. Quote each
    # argument with the Microsoft C-runtime rules so spaces, quotes, and trailing slashes
    # survive as one argv element in the native child.
    $Builder = [Text.StringBuilder]::new()
    [void] $Builder.Append([char] 34)
    $Backslashes = 0
    foreach ($Character in $Argument.ToCharArray()) {
        if ($Character -eq [char] 92) {
            $Backslashes += 1
            continue
        }
        if ($Character -eq [char] 34) {
            [void] $Builder.Append([char] 92, (2 * $Backslashes) + 1)
            [void] $Builder.Append([char] 34)
            $Backslashes = 0
            continue
        }
        if ($Backslashes -gt 0) {
            [void] $Builder.Append([char] 92, $Backslashes)
            $Backslashes = 0
        }
        [void] $Builder.Append($Character)
    }
    if ($Backslashes -gt 0) {
        [void] $Builder.Append([char] 92, 2 * $Backslashes)
    }
    [void] $Builder.Append([char] 34)
    return $Builder.ToString()
}

$ResolvedWorkingDirectory = [IO.Path]::GetFullPath($WorkingDirectory)
if (-not (Test-Path -LiteralPath $ResolvedWorkingDirectory -PathType Container)) {
    throw 'IDIS_NATIVE_PROCESS_WORKING_DIRECTORY_MISSING'
}

$ResolvedStdout = Resolve-FreshCapturePath `
    -Path $StandardOutputPath `
    -FailurePrefix 'IDIS_NATIVE_PROCESS_STDOUT'
$ResolvedStderr = Resolve-FreshCapturePath `
    -Path $StandardErrorPath `
    -FailurePrefix 'IDIS_NATIVE_PROCESS_STDERR'
if ([string]::Equals($ResolvedStdout, $ResolvedStderr, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'IDIS_NATIVE_PROCESS_CAPTURE_PATHS_MUST_DIFFER'
}
$QuotedArgumentList = @(
    $ArgumentList | ForEach-Object { ConvertTo-WindowsNativeArgument -Argument $_ }
)

$StartParameters = @{
    FilePath               = $FilePath
    ArgumentList           = $QuotedArgumentList
    WorkingDirectory       = $ResolvedWorkingDirectory
    RedirectStandardOutput = $ResolvedStdout
    RedirectStandardError  = $ResolvedStderr
    WindowStyle            = 'Hidden'
    PassThru               = $true
}
if (-not $Background) {
    $StartParameters['Wait'] = $true
}
$Process = Start-Process @StartParameters

if ($Background) {
    [pscustomobject] @{
        SchemaVersion     = 2
        Mode              = 'background'
        LauncherProcessId = [int] $Process.Id
        ExitCode          = $null
    }
    return
}

$Process.Refresh()
$ExitCode = [int] $Process.ExitCode
if ($ExitCode -ne 0) {
    throw "IDIS_NATIVE_PROCESS_EXIT_$ExitCode"
}

[pscustomobject] @{
    SchemaVersion = 1
    Mode          = 'foreground'
    ProcessId     = [int] $Process.Id
    ExitCode      = $ExitCode
}
