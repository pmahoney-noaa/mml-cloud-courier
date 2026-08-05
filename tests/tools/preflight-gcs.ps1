<#
.SYNOPSIS
  Read-only preflight for the Plan 2 release gate.

.DESCRIPTION
  Reports what GCP state exists and prints the exact command to fix anything
  missing. Creates no billable resources: the one object it writes is a
  permission probe that it deletes again.

  -Prefix names a scratch folder inside an existing bucket. The gate confines
  every object it writes to that folder, so an in-use bucket is a valid target.

.EXAMPLE
  pwsh tests/tools/preflight-gcs.ps1 -Bucket mmlct-gate-test

.EXAMPLE
  pwsh tests/tools/preflight-gcs.ps1 -Bucket my-research-bucket -Prefix scratch/mmlct
#>
param(
    [Parameter(Mandatory = $true)][string]$Bucket,
    [string]$Prefix,
    [string]$Project
)

# Normalise: no leading slash, exactly one trailing slash, or empty.
$PrefixPath = if ($Prefix) { $Prefix.Trim('/') + '/' } else { "" }

$ErrorActionPreference = "Stop"
$script:Failed = $false

function Report-Ok   ($msg) { Write-Host "  OK    $msg" -ForegroundColor Green }
function Report-Warn ($msg) { Write-Host "  WARN  $msg" -ForegroundColor Yellow }
function Report-Fail ($msg, $fix) {
    Write-Host "  FAIL  $msg" -ForegroundColor Red
    Write-Host "        fix: $fix" -ForegroundColor Cyan
    $script:Failed = $true
}

function Invoke-Gcloud {
    # Runs gcloud and returns [ExitCode, Output] without throwing.
    $output = & gcloud @args 2>&1 | Out-String
    return @($LASTEXITCODE, $output.Trim())
}

Write-Host "`nPlan 2 release-gate preflight" -ForegroundColor White
Write-Host "bucket: $Bucket"
Write-Host ("scratch prefix: " + $(if ($PrefixPath) { $PrefixPath } else { "(bucket root)" }))
Write-Host ""

# 1. gcloud present
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) {
    Report-Fail "gcloud is not on PATH" "install from https://cloud.google.com/sdk/docs/install"
    Write-Host "`nStopping: every remaining check needs gcloud.`n"
    exit 1
}
Report-Ok "gcloud is installed"

# 2. Authenticated
$code, $accounts = Invoke-Gcloud auth list --filter=status:ACTIVE --format="value(account)"
if ($code -ne 0 -or -not $accounts) {
    Report-Fail "no active gcloud account" "gcloud auth login"
} else {
    Report-Ok "authenticated as $accounts"
}

# 3. Application Default Credentials — this is what the tests actually use
$code, $null = Invoke-Gcloud auth application-default print-access-token
if ($code -ne 0) {
    Report-Fail "Application Default Credentials are not set up" `
                "gcloud auth application-default login"
} else {
    Report-Ok "Application Default Credentials work"
}

# 4. Project
if (-not $Project) {
    $code, $Project = Invoke-Gcloud config get-value project
    if ($code -ne 0 -or -not $Project -or $Project -eq "(unset)") { $Project = $null }
}
if (-not $Project) {
    Report-Fail "no project configured" "gcloud config set project <project-id>"
} else {
    Report-Ok "project is $Project"
}

# 5-8. Bucket metadata: BEST EFFORT, never fatal.
#
# storage.buckets.get is a separate permission from object access, and the
# project spec RECOMMENDS least-privilege object-level-only credentials
# ("object-level access to a single bucket"). Failing here would reject the
# very IAM shape the product tells users to adopt. When metadata is
# unreadable we say so and move on -- the permission probe below is the
# authoritative check, because it exercises the operations the gate needs
# rather than describing them.
$code, $describe = Invoke-Gcloud storage buckets describe "gs://$Bucket" --format=json
if ($code -ne 0) {
    Report-Warn ("cannot read bucket metadata (storage.buckets.get denied, or the " +
                 "bucket does not exist) — skipping storage-class, versioning, " +
                 "retention and lifecycle checks; the permission probe below decides")
    Report-Warn ("  unverified: storage class (cold-storage minimum-duration charges), " +
                 "object versioning (deletes leave billable noncurrent versions), " +
                 "retention policy (deletes refused outright)")
} else {
    Report-Ok "bucket gs://$Bucket metadata is readable"
    $meta = $describe | ConvertFrom-Json
    if ($meta.default_storage_class -ne "STANDARD") {
        Report-Warn ("storage class is $($meta.default_storage_class) — temp slice objects " +
                     "will incur minimum-storage-duration charges (spec: cold-storage risk)")
    } else {
        Report-Ok "storage class is STANDARD"
    }

    # 6. Object versioning — teardown's deletes would become noncurrent versions,
    #    so the gate's bytes would keep billing and "the bucket is clean" would
    #    be false even though every assertion passed.
    if ($meta.versioning_enabled -eq $true) {
        Report-Warn ("object versioning is ENABLED — the gate's deletes leave noncurrent " +
                     "versions that keep billing; add a noncurrent-version lifecycle rule " +
                     "or expect to purge them manually")
    } else {
        Report-Ok "object versioning is disabled"
    }

    # 7. Retention policy / bucket lock — this one is fatal rather than costly:
    #    deletes are refused, so the fixture's teardown cannot clean up and its
    #    emptiness assertion fails the whole session.
    if ($meta.retention_policy) {
        Report-Fail ("bucket has a retention policy " +
                     "($($meta.retention_policy.retentionPeriod)s) — the gate cannot " +
                     "delete what it writes") `
                    "run the gate against a bucket without a retention policy"
    } else {
        Report-Ok "no retention policy"
    }

    # 8. Lifecycle safety net for orphaned slice temps
    $rules = $meta.lifecycle_config.rule
    $hasTmpRule = $false
    foreach ($rule in $rules) {
        if ($rule.condition.matchesPrefix -and
            ($rule.condition.matchesPrefix -join " ") -match "mmlct") { $hasTmpRule = $true }
    }
    if (-not $hasTmpRule) {
        Report-Warn "no lifecycle rule covering mmlct-gate/ orphans — see the gate record for the JSON"
    } else {
        Report-Ok "lifecycle rule covering mmlct objects is present"
    }
}

# 9. Permission probe — THE authoritative check.
#
# Exercises the four object operations the gate depends on, inside the scratch
# prefix. This is what decides the exit code: it proves the operations rather
# than describing the IAM that might allow them, and its delete step is also
# the real test for a retention policy, which metadata could not tell us about.
if (-not $script:Failed) {
    $probePrefix = "$PrefixPath" + "mmlct-preflight/$([guid]::NewGuid().ToString('N').Substring(0,8))"
    $tmp = Join-Path $env:TEMP "mmlct-probe.bin"
    $wrote = $false
    try {
        Set-Content -Path $tmp -Value "mmlct preflight probe" -NoNewline
        $code, $out = Invoke-Gcloud storage cp $tmp "gs://$Bucket/$probePrefix/a.bin"
        if ($code -ne 0) {
            Report-Fail "cannot write to gs://$Bucket/$PrefixPath — $($out -split "`n" | Select-Object -First 1)" `
                        "confirm the bucket exists and grant roles/storage.objectAdmin on it to $accounts"
        } else {
            $wrote = $true
            Report-Ok "write succeeded"
            $code, $out = Invoke-Gcloud storage cp $tmp "gs://$Bucket/$probePrefix/b.bin"
            if ($code -ne 0) {
                Report-Fail "cannot write gs://$Bucket/$probePrefix/b.bin — $($out -split "`n" | Select-Object -First 1)" `
                            "confirm the bucket exists and grant roles/storage.objectAdmin on it to $accounts"
            } else {
                $code, $out = Invoke-Gcloud storage objects compose `
                    "gs://$Bucket/$probePrefix/a.bin" "gs://$Bucket/$probePrefix/b.bin" `
                    "gs://$Bucket/$probePrefix/composed.bin"
                if ($code -ne 0) {
                    Report-Fail "compose is not permitted" `
                                "grant roles/storage.objectAdmin (compose needs create + get)"
                } else {
                    Report-Ok "compose succeeded"
                }
            }
        }
    } finally {
        Remove-Item $tmp -ErrorAction SilentlyContinue
        if ($wrote) {
            # Deleting is not cleanup hygiene here — it is a check. A retention
            # policy or object hold refuses deletes, and the gate's fixture
            # teardown would then fail the whole session after writing 2.6 GiB.
            Invoke-Gcloud storage rm --recursive "gs://$Bucket/$probePrefix" | Out-Null
            $code, $survivors = Invoke-Gcloud storage ls --recursive "gs://$Bucket/$probePrefix"
            if ($survivors -and $survivors -notmatch "One or more URLs matched no objects") {
                Report-Fail "probe objects could not be deleted: $survivors" `
                            "check for a retention policy, object hold, or missing storage.objects.delete"
            } else {
                Report-Ok "probe objects deleted (deletes are permitted)"
            }
        }
    }
}

Write-Host ""
if ($script:Failed) {
    Write-Host "Preflight FAILED — fix the items above and run again.`n" -ForegroundColor Red
    exit 1
}
Write-Host "Preflight passed. Run the gate with:" -ForegroundColor Green
Write-Host ""
Write-Host "  `$env:MMLCT_TEST_BUCKET = `"$Bucket`""
if ($PrefixPath) {
    Write-Host "  `$env:MMLCT_TEST_PREFIX = `"$($PrefixPath.TrimEnd('/'))`""
}
Write-Host ""
exit 0
