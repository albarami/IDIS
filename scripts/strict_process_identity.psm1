Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:StrictProcessIdentityMaxDepth = 32
$script:StrictProcessIdentityPollMilliseconds = 250
$script:StrictProcessIdentityTimeoutSeconds = 60

function Resolve-StrictProcessIdentityFile {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path,

        [Parameter(Mandatory = $true)]
        [string] $FailureCode
    )

    try {
        $Candidate = [IO.Path]::GetFullPath($Path)
        if (-not (Test-Path -LiteralPath $Candidate -PathType Leaf)) {
            throw $FailureCode
        }
        return (Resolve-Path -LiteralPath $Candidate -ErrorAction Stop).ProviderPath
    }
    catch {
        throw $FailureCode
    }
}

function Get-StrictProcessIdentitySha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Path
    )

    try {
        return (Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
    }
    catch {
        throw 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    }
}

function Get-StrictCimProcess {
    param(
        [Parameter(Mandatory = $true)]
        [int] $ProcessId
    )

    try {
        return Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $ProcessId" `
            -ErrorAction Stop
    }
    catch {
        throw 'IDIS_NATIVE_PROCESS_INSPECTION_FAILED'
    }
}

function Convert-StrictProcessCreationTime {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Value
    )

    try {
        if ($Value -is [DateTime]) {
            return ([DateTime] $Value).ToUniversalTime()
        }
        return [Management.ManagementDateTimeConverter]::ToDateTime([string] $Value).ToUniversalTime()
    }
    catch {
        throw 'IDIS_NATIVE_PROCESS_INSPECTION_FAILED'
    }
}

function Resolve-StrictExpectedProcessExecutables {
    param(
        [Parameter(Mandatory = $true)]
        [string] $ExpectedLauncherPath
    )

    $LauncherPath = Resolve-StrictProcessIdentityFile `
        -Path $ExpectedLauncherPath `
        -FailureCode 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    $ServicePath = $LauncherPath
    $PyvenvConfigSha256 = $null

    $ScriptsDirectory = [IO.Path]::GetDirectoryName($LauncherPath)
    $EnvironmentRoot = if ([string]::IsNullOrWhiteSpace($ScriptsDirectory)) {
        $null
    }
    else {
        [IO.Path]::GetDirectoryName($ScriptsDirectory)
    }
    $PyvenvConfig = if ([string]::IsNullOrWhiteSpace($EnvironmentRoot)) {
        $null
    }
    else {
        Join-Path $EnvironmentRoot 'pyvenv.cfg'
    }

    if ($null -ne $PyvenvConfig -and (Test-Path -LiteralPath $PyvenvConfig -PathType Leaf)) {
        try {
            $ExecutableValues = @()
            foreach ($Line in [IO.File]::ReadAllLines($PyvenvConfig, [Text.Encoding]::UTF8)) {
                $Match = [regex]::Match($Line, '^\s*executable\s*=\s*(.+?)\s*$')
                if ($Match.Success) {
                    $ExecutableValues += $Match.Groups[1].Value
                }
            }
            if ($ExecutableValues.Count -ne 1 -or -not [IO.Path]::IsPathRooted($ExecutableValues[0])) {
                throw 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
            }
            $ServicePath = Resolve-StrictProcessIdentityFile `
                -Path $ExecutableValues[0] `
                -FailureCode 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
            $PyvenvConfigSha256 = Get-StrictProcessIdentitySha256 -Path $PyvenvConfig
        }
        catch {
            throw 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
        }
    }

    [pscustomobject] @{
        LauncherPath        = $LauncherPath
        ServicePath         = $ServicePath
        LauncherSha256      = Get-StrictProcessIdentitySha256 -Path $LauncherPath
        ServiceSha256       = Get-StrictProcessIdentitySha256 -Path $ServicePath
        PyvenvConfigSha256  = $PyvenvConfigSha256
    }
}

function New-StrictProcessSnapshot {
    param(
        [Parameter(Mandatory = $true)]
        [object] $Process
    )

    [pscustomobject] [ordered] @{
        ProcessId       = [int] $Process.ProcessId
        ParentProcessId = [int] $Process.ParentProcessId
        CreationTimeUtc = Convert-StrictProcessCreationTime -Value $Process.CreationDate
    }
}

function Get-StrictServiceProcessIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, [int]::MaxValue)]
        [int] $LauncherProcessId,

        [Parameter(Mandatory = $true)]
        [ValidateRange(1, [int]::MaxValue)]
        [int] $ServiceProcessId,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $ExpectedLauncherPath
    )

    if ($LauncherProcessId -eq $ServiceProcessId) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
    }

    $Expected = Resolve-StrictExpectedProcessExecutables `
        -ExpectedLauncherPath $ExpectedLauncherPath
    $Launcher = Get-StrictCimProcess -ProcessId $LauncherProcessId
    if ($null -eq $Launcher) {
        throw 'IDIS_NATIVE_PROCESS_LAUNCHER_NOT_LIVE'
    }
    $Service = Get-StrictCimProcess -ProcessId $ServiceProcessId
    if ($null -eq $Service) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_NOT_LIVE'
    }

    $LauncherExecutable = Resolve-StrictProcessIdentityFile `
        -Path ([string] $Launcher.ExecutablePath) `
        -FailureCode 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    if (-not [string]::Equals(
        $LauncherExecutable,
        $Expected.LauncherPath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    }
    $ServiceExecutable = Resolve-StrictProcessIdentityFile `
        -Path ([string] $Service.ExecutablePath) `
        -FailureCode 'IDIS_NATIVE_PROCESS_SERVICE_EXECUTABLE_MISMATCH'
    if (-not [string]::Equals(
        $ServiceExecutable,
        $Expected.ServicePath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_EXECUTABLE_MISMATCH'
    }

    $LauncherSnapshot = New-StrictProcessSnapshot -Process $Launcher
    $ServiceSnapshot = New-StrictProcessSnapshot -Process $Service
    if ($ServiceSnapshot.CreationTimeUtc -lt $LauncherSnapshot.CreationTimeUtc) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
    }

    $Seen = [Collections.Generic.HashSet[int]]::new()
    [void] $Seen.Add($ServiceProcessId)
    $LineageSnapshots = [Collections.Generic.List[object]]::new()
    $LineageSnapshots.Add($ServiceSnapshot)
    $Current = $Service
    $CurrentSnapshot = $ServiceSnapshot
    $LineageDepth = 0
    $FoundLauncher = $false
    while (-not $FoundLauncher) {
        if ($LineageDepth -ge $script:StrictProcessIdentityMaxDepth) {
            throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
        }
        $ParentProcessId = [int] $Current.ParentProcessId
        if ($ParentProcessId -le 0 -or -not $Seen.Add($ParentProcessId)) {
            throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
        }
        $Parent = if ($ParentProcessId -eq $LauncherProcessId) {
            $Launcher
        }
        else {
            Get-StrictCimProcess -ProcessId $ParentProcessId
        }
        if ($null -eq $Parent) {
            throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
        }
        $ParentSnapshot = New-StrictProcessSnapshot -Process $Parent
        if ($ParentSnapshot.CreationTimeUtc -gt $CurrentSnapshot.CreationTimeUtc) {
            throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_UNRELATED'
        }
        $LineageSnapshots.Add($ParentSnapshot)
        $LineageDepth += 1
        if ($ParentProcessId -eq $LauncherProcessId) {
            $FoundLauncher = $true
            break
        }
        $Current = $Parent
        $CurrentSnapshot = $ParentSnapshot
    }

    foreach ($Snapshot in $LineageSnapshots) {
        $Recheck = Get-StrictCimProcess -ProcessId $Snapshot.ProcessId
        if ($null -eq $Recheck) {
            throw 'IDIS_NATIVE_PROCESS_INSPECTION_FAILED'
        }
        $RecheckSnapshot = New-StrictProcessSnapshot -Process $Recheck
        if (
            $RecheckSnapshot.ParentProcessId -ne $Snapshot.ParentProcessId `
            -or $RecheckSnapshot.CreationTimeUtc -ne $Snapshot.CreationTimeUtc
        ) {
            throw 'IDIS_NATIVE_PROCESS_INSPECTION_FAILED'
        }
    }
    $LauncherRecheck = Get-StrictCimProcess -ProcessId $LauncherProcessId
    $ServiceRecheck = Get-StrictCimProcess -ProcessId $ServiceProcessId
    if ($null -eq $LauncherRecheck) {
        throw 'IDIS_NATIVE_PROCESS_LAUNCHER_NOT_LIVE'
    }
    if ($null -eq $ServiceRecheck) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_NOT_LIVE'
    }
    $LauncherExecutableRecheck = Resolve-StrictProcessIdentityFile `
        -Path ([string] $LauncherRecheck.ExecutablePath) `
        -FailureCode 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    $ServiceExecutableRecheck = Resolve-StrictProcessIdentityFile `
        -Path ([string] $ServiceRecheck.ExecutablePath) `
        -FailureCode 'IDIS_NATIVE_PROCESS_SERVICE_EXECUTABLE_MISMATCH'
    if (-not [string]::Equals(
        $LauncherExecutableRecheck,
        $Expected.LauncherPath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'IDIS_NATIVE_PROCESS_EXPECTED_EXECUTABLE_INVALID'
    }
    if (-not [string]::Equals(
        $ServiceExecutableRecheck,
        $Expected.ServicePath,
        [StringComparison]::OrdinalIgnoreCase
    )) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_EXECUTABLE_MISMATCH'
    }

    [pscustomobject] [ordered] @{
        SchemaVersion                     = 1
        LauncherProcessId                 = $LauncherProcessId
        ServiceProcessId                  = $ServiceProcessId
        LineageDepth                      = $LineageDepth
        PidRelationship                   = 'launcher_ancestor'
        PidRelationshipValidated          = $true
        LauncherExecutableValidated       = $true
        ServiceExecutableValidated        = $true
        LauncherExecutableSha256          = $Expected.LauncherSha256
        ServiceExecutableSha256           = $Expected.ServiceSha256
        PyvenvConfigSha256                = $Expected.PyvenvConfigSha256
    }
}

function Wait-StrictServiceProcessIdentity {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, [int]::MaxValue)]
        [int] $LauncherProcessId,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $ServicePidFilePath,

        [Parameter(Mandatory = $true)]
        [ValidateNotNullOrEmpty()]
        [string] $ExpectedLauncherPath
    )

    $PidFile = [IO.Path]::GetFullPath($ServicePidFilePath)
    $LauncherAtStart = Get-StrictCimProcess -ProcessId $LauncherProcessId
    if ($null -eq $LauncherAtStart) {
        throw 'IDIS_NATIVE_PROCESS_LAUNCHER_NOT_LIVE'
    }
    $LauncherSnapshot = New-StrictProcessSnapshot -Process $LauncherAtStart
    $Deadline = [DateTime]::UtcNow.AddSeconds($script:StrictProcessIdentityTimeoutSeconds)
    $SawInvalidPidFile = $false
    do {
        $LauncherCurrent = Get-StrictCimProcess -ProcessId $LauncherProcessId
        if ($null -eq $LauncherCurrent) {
            throw 'IDIS_NATIVE_PROCESS_LAUNCHER_NOT_LIVE'
        }
        $LauncherCurrentSnapshot = New-StrictProcessSnapshot -Process $LauncherCurrent
        if (
            $LauncherCurrentSnapshot.ParentProcessId -ne $LauncherSnapshot.ParentProcessId `
            -or $LauncherCurrentSnapshot.CreationTimeUtc -ne $LauncherSnapshot.CreationTimeUtc
        ) {
            throw 'IDIS_NATIVE_PROCESS_INSPECTION_FAILED'
        }
        if (Test-Path -LiteralPath $PidFile -PathType Leaf) {
            $FileBefore = $null
            $RawServiceProcessId = $null
            try {
                $FileBefore = Get-Item -LiteralPath $PidFile -Force -ErrorAction Stop
                $RawServiceProcessId = [IO.File]::ReadAllText(
                    $PidFile,
                    [Text.Encoding]::ASCII
                ).Trim()
                $FileAfterRead = Get-Item -LiteralPath $PidFile -Force -ErrorAction Stop
            }
            catch [IO.IOException] {
                $FileBefore = $null
            }
            catch [UnauthorizedAccessException] {
                $FileBefore = $null
            }
            catch {
                throw
            }
            if (
                $null -ne $FileBefore `
                -and $FileBefore.LastWriteTimeUtc -ge $LauncherSnapshot.CreationTimeUtc `
                -and $FileBefore.Length -eq $FileAfterRead.Length `
                -and $FileBefore.LastWriteTimeUtc -eq $FileAfterRead.LastWriteTimeUtc
            ) {
                $CandidateServiceProcessId = 0
                if (-not (
                    [int]::TryParse($RawServiceProcessId, [ref] $CandidateServiceProcessId) `
                    -and $CandidateServiceProcessId -gt 0
                )) {
                    $SawInvalidPidFile = $true
                }
                else {
                    $Service = Get-StrictCimProcess -ProcessId $CandidateServiceProcessId
                    if ($null -ne $Service) {
                        try {
                            $Identity = Get-StrictServiceProcessIdentity `
                                -LauncherProcessId $LauncherProcessId `
                                -ServiceProcessId $CandidateServiceProcessId `
                                -ExpectedLauncherPath $ExpectedLauncherPath
                        }
                        catch {
                            if ($_.Exception.Message -eq 'IDIS_NATIVE_PROCESS_SERVICE_NOT_LIVE') {
                                $Identity = $null
                            }
                            else {
                                throw
                            }
                        }
                        if ($null -ne $Identity) {
                            try {
                                $FileAfterValidation = Get-Item `
                                    -LiteralPath $PidFile `
                                    -Force `
                                    -ErrorAction Stop
                                $RawAfterValidation = [IO.File]::ReadAllText(
                                    $PidFile,
                                    [Text.Encoding]::ASCII
                                ).Trim()
                            }
                            catch [IO.IOException] {
                                $FileAfterValidation = $null
                            }
                            catch [UnauthorizedAccessException] {
                                $FileAfterValidation = $null
                            }
                            catch {
                                throw
                            }
                            if (
                                $null -ne $FileAfterValidation `
                                -and $FileAfterValidation.LastWriteTimeUtc -ge $LauncherSnapshot.CreationTimeUtc `
                                -and $FileAfterValidation.Length -eq $FileAfterRead.Length `
                                -and $FileAfterValidation.LastWriteTimeUtc -eq $FileAfterRead.LastWriteTimeUtc `
                                -and $RawAfterValidation -eq $RawServiceProcessId
                            ) {
                                return $Identity
                            }
                        }
                    }
                }
            }
        }
        Start-Sleep -Milliseconds $script:StrictProcessIdentityPollMilliseconds
    } while ([DateTime]::UtcNow -lt $Deadline)

    if ($SawInvalidPidFile) {
        throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_FILE_INVALID'
    }
    throw 'IDIS_NATIVE_PROCESS_SERVICE_PID_FILE_TIMEOUT'
}

Export-ModuleMember -Function @(
    'Get-StrictServiceProcessIdentity',
    'Wait-StrictServiceProcessIdentity'
)
