# Exercise runner exit codes in a child shell without recursively running Pester.
Describe 'Test runner' {
    $runner = Join-Path (Split-Path -Parent $PSScriptRoot) 'tests\Invoke-Tests.ps1'
    $shell = (Get-Process -Id $PID).Path
    $modules = Join-Path $TestDrive 'Modules'
    $childOutput = Join-Path $TestDrive 'runner-output.txt'
    $childError = Join-Path $TestDrive 'runner-error.txt'
    $pesterDir = Join-Path $modules 'Pester'
    $analyzerDir = Join-Path $modules 'PSScriptAnalyzer'
    New-Item -ItemType Directory -Path $pesterDir, $analyzerDir -Force | Out-Null
    @'
function Invoke-Pester {
    param($Path, [switch]$PassThru)
    [pscustomobject]@{
        TotalCount = [int]$env:SRG_TEST_TOTAL
        PassedCount = [int]$env:SRG_TEST_TOTAL - [int]$env:SRG_TEST_FAILED
        FailedCount = [int]$env:SRG_TEST_FAILED
        SkippedCount = 0
        TestResult = @()
    }
}
Export-ModuleMember Invoke-Pester
'@ | Set-Content (Join-Path $pesterDir 'Pester.psm1')
    New-ModuleManifest -Path (Join-Path $pesterDir 'Pester.psd1') -RootModule 'Pester.psm1' -ModuleVersion '4.99.0'
    @'
function Invoke-ScriptAnalyzer {
    param($Path, [switch]$Recurse, $Severity, $ExcludeRule)
    for ($i = 0; $i -lt [int]$env:SRG_TEST_ISSUES; $i++) {
        [pscustomobject]@{ RuleName='FixtureRule'; Severity='Warning'; ScriptName='fixture.ps1'; Line=1; Message='fixture' }
    }
}
Export-ModuleMember Invoke-ScriptAnalyzer
'@ | Set-Content (Join-Path $analyzerDir 'PSScriptAnalyzer.psm1')
    New-ModuleManifest -Path (Join-Path $analyzerDir 'PSScriptAnalyzer.psd1') -RootModule 'PSScriptAnalyzer.psm1' -ModuleVersion '99.0.0'

    BeforeEach {
        $script:savedModulePath = $env:PSModulePath
        $script:savedTotal = $env:SRG_TEST_TOTAL
        $script:savedFailed = $env:SRG_TEST_FAILED
        $script:savedIssues = $env:SRG_TEST_ISSUES
        $env:PSModulePath = $modules + [IO.Path]::PathSeparator + (Join-Path $PSHOME 'Modules')
        $env:SRG_TEST_TOTAL = '1'
        $env:SRG_TEST_FAILED = '0'
        $env:SRG_TEST_ISSUES = '0'
    }
    AfterEach {
        $env:PSModulePath = $script:savedModulePath
        $env:SRG_TEST_TOTAL = $script:savedTotal
        $env:SRG_TEST_FAILED = $script:savedFailed
        $env:SRG_TEST_ISSUES = $script:savedIssues
    }

    It 'succeeds with passing tests and zero analyzer findings' {
        $child = Start-Process -FilePath $shell -ArgumentList '-NoProfile', '-File', "`"$runner`"" -Wait -PassThru -RedirectStandardOutput $childOutput -RedirectStandardError $childError
        if ($child.ExitCode -ne 0) { Get-Content $childOutput, $childError | Write-Host }
        $child.ExitCode | Should Be 0
    }
    It 'fails with exactly one analyzer finding' {
        $env:SRG_TEST_ISSUES = '1'
        $child = Start-Process -FilePath $shell -ArgumentList '-NoProfile', '-File', "`"$runner`"" -Wait -PassThru -RedirectStandardOutput $childOutput -RedirectStandardError $childError
        $child.ExitCode | Should Be 1
    }
    It 'fails when tests fail' {
        $env:SRG_TEST_FAILED = '1'
        $child = Start-Process -FilePath $shell -ArgumentList '-NoProfile', '-File', "`"$runner`"", '-NoAnalyzer' -Wait -PassThru -RedirectStandardOutput $childOutput -RedirectStandardError $childError
        $child.ExitCode | Should Be 1
    }
    It 'fails rather than succeeding when no tests are discovered' {
        $env:SRG_TEST_TOTAL = '0'
        $child = Start-Process -FilePath $shell -ArgumentList '-NoProfile', '-File', "`"$runner`"", '-NoAnalyzer' -Wait -PassThru -RedirectStandardOutput $childOutput -RedirectStandardError $childError
        $child.ExitCode | Should Not Be 0
    }
    It 'can explicitly skip analyzer findings for a tests-only run' {
        $env:SRG_TEST_ISSUES = '1'
        $child = Start-Process -FilePath $shell -ArgumentList '-NoProfile', '-File', "`"$runner`"", '-NoAnalyzer' -Wait -PassThru -RedirectStandardOutput $childOutput -RedirectStandardError $childError
        if ($child.ExitCode -ne 0) { Get-Content $childOutput, $childError | Write-Host }
        $child.ExitCode | Should Be 0
    }
}
