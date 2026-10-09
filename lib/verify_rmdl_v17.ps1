# Structural validator for S21 client mdl_ v17 (.rmdl) files.
#
# Checks the tables whose corruption is invisible to a size/hash compare but fatal
# at runtime: the attachment table, and every 2-byte name offset in the file.
#
# The engine decodes 2-byte offsets as FIX(o) = (o & 0xFFFE) << (4 * (o & 1)).
# An ODD offset is therefore NOT "offset o" -- it is the compact large-offset form
# and decodes to (o - 1) * 16. Respawn's writer pads strings so every offset is even.
#
# Usage:  powershell -File verify_rmdl_v17.ps1 <file-or-directory> [-Quiet]

param(
    [Parameter(Mandatory = $true)][string]$Path,
    [switch]$Quiet
)

function Get-Fix([int]$o) { return ($o -band 0xFFFE) -shl (4 * ($o -band 1)) }

function Read-CString($d, [int]$at, [int]$max = 64) {
    if ($at -lt 0 -or $at -ge $d.Length) { return $null }
    $s = ''
    for ($j = $at; $j -lt $d.Length -and $j - $at -lt $max; $j++) {
        if ($d[$j] -eq 0) { return $s }
        $s += [char]$d[$j]
    }
    return $null   # unterminated within max
}

function Test-Printable([string]$s) {
    if ($null -eq $s -or $s.Length -eq 0) { return $false }
    foreach ($ch in $s.ToCharArray()) {
        if ([int]$ch -lt 0x20 -or [int]$ch -gt 0x7E) { return $false }
    }
    return $true
}

# Returns a hashtable of results for one file.
function Test-Rmdl([string]$file) {
    $r = [ordered]@{ file = $file; errors = @(); warnings = @() }
    $d = [System.IO.File]::ReadAllBytes($file)
    if ($d.Length -lt 0xE4) { $r.errors += "file shorter than a 228-byte v17 header"; return $r }

    $u16 = { param($off) [BitConverter]::ToUInt16($d, $off) }

    $r.size      = $d.Length
    $numatt      = $d[0x83]
    $attIdx      = & $u16 0x84
    $hbIdx       = & $u16 0x34
    $numhbset    = $d[0x36]
    $boneCount   = & $u16 0x74
    $boneHdrOff  = & $u16 0x76
    $numNodes    = & $u16 0x86
    $nodeNameIdx = & $u16 0x88
    $numPose     = & $u16 0xA2
    $poseIdx     = & $u16 0xA4
    $numSbt      = & $u16 0xC4
    $sbtIdx      = & $u16 0xC6

    $r.numatt = $numatt; $r.boneCount = $boneCount

    # --- D1: attachment table must not alias the hitbox table -------------------
    if ($numatt -gt 0 -and $numhbset -gt 0 -and $attIdx -eq $hbIdx) {
        $r.errors += ("localattachmentindex aliases hitboxsetindex (both 0x{0:X}) with numlocalattachments={1}" -f $attIdx, $numatt)
    }

    # --- attachment table walk ---------------------------------------------------
    if ($numatt -gt 0) {
        $base = Get-Fix $attIdx
        $names = @()
        for ($i = 0; $i -lt $numatt; $i++) {
            $e = $base + 56 * $i
            if ($e + 56 -gt $d.Length) { $r.errors += "attachment[$i] entry out of bounds (0x$($e.ToString('X')))"; break }
            $so = [BitConverter]::ToUInt16($d, $e)
            $bone = [BitConverter]::ToUInt16($d, $e + 2)
            if ($so -band 1) { $r.errors += "attachment[$i] name offset 0x$($so.ToString('X')) is ODD" }
            $na = $e + (Get-Fix $so)
            $nm = Read-CString $d $na
            if (-not (Test-Printable $nm)) {
                $r.errors += "attachment[$i] name unresolvable at 0x$($na.ToString('X'))"
            } else { $names += $nm }
            if ($boneCount -gt 0 -and $bone -ge $boneCount) {
                $r.errors += "attachment[$i] ('$nm') localbone=$bone >= boneCount=$boneCount"
            }
        }
        $r.attNames = $names
    }

    # --- D2: bone name offsets must all be even and resolvable -------------------
    if ($boneCount -gt 0) {
        $bh = Get-Fix $boneHdrOff
        $odd = 0; $bad = 0
        for ($i = 0; $i -lt $boneCount; $i++) {
            $e = $bh + 12 * $i
            if ($e + 12 -gt $d.Length) { $bad++; continue }
            $so = [BitConverter]::ToUInt16($d, $e + 10)
            if ($so -band 1) { $odd++ }
            $nm = Read-CString $d ($e + (Get-Fix $so))
            if (-not (Test-Printable $nm)) { $bad++ }
        }
        $r.oddBoneNames = $odd
        if ($odd -gt 0) { $r.errors += "$odd/$boneCount bone name offsets are ODD" }
        if ($bad -gt 0) { $r.errors += "$bad/$boneCount bone names unresolvable" }
    }

    # --- D3: a multi-bone, non-static-prop model must claim a virtual model -------
    # Bit 19 of studiohdr.flags tells the engine the model's sequences come from a
    # virtual model. With it clear the model cache hands back no virtual model at all,
    # so the animrig is never bound and the model renders correctly but never animates.
    # Across 623 stock v17 models the bit is set on every multi-bone non-static-prop
    # model and on no single-bone one.
    $flags = [BitConverter]::ToUInt32($d, 0x00)
    $r.flags = $flags
    $isStaticProp = ($flags -band 0x10) -ne 0
    $usesVirtualModel = ($flags -band 0x80000) -ne 0
    # Warning, not an error: 15 of the 623 stock models are boned physics props and
    # gibs that legitimately have no animrig. Anything that is meant to play sequences
    # and lands here is broken.
    if ($boneCount -gt 1 -and -not $isStaticProp -and -not $usesVirtualModel) {
        $r.warnings += ("flags=0x{0:X8} lacks 0x80000 with boneCount={1} -- cannot animate (fine only for a rigless physics prop)" -f $flags, $boneCount)
    }
    if ($usesVirtualModel -and $isStaticProp) {
        $r.errors += ("flags=0x{0:X8} sets both 0x80000 and STATIC_PROP" -f $flags)
    }

    # --- D4: no count may survive without its table ------------------------------
    if ($numNodes -gt 0 -and $nodeNameIdx -eq 0) { $r.errors += "numlocalnodes=$numNodes but localnodenameindex=0" }
    if ($numPose  -gt 0 -and $poseIdx    -eq 0) { $r.errors += "numlocalposeparameters=$numPose but localposeparamindex=0" }
    if ($numSbt   -gt 0 -and $sbtIdx     -eq 0) { $r.errors += "numsrcbonetransform=$numSbt but srcbonetransformindex=0" }

    return $r
}

$targets = @()
if (Test-Path $Path -PathType Container) {
    $targets = Get-ChildItem -Path $Path -Filter *.rmdl -Recurse -File | ForEach-Object { $_.FullName }
} else { $targets = @($Path) }

$pass = 0; $fail = 0; $withAtt = 0; $warned = 0
foreach ($t in $targets) {
    $r = Test-Rmdl $t
    if ($r.numatt -gt 0) { $withAtt++ }
    if ($r.warnings.Count -gt 0) {
        $warned++
        Write-Output ("WARN  {0}" -f (Split-Path $t -Leaf))
        foreach ($w in $r.warnings) { Write-Output "        $w" }
    }
    if ($r.errors.Count -eq 0) {
        $pass++
        if (-not $Quiet) {
            Write-Output ("PASS  {0}  (att={1} bones={2} flags=0x{3:X8})" -f (Split-Path $t -Leaf), $r.numatt, $r.boneCount, $r.flags)
            if ($r.attNames -and $r.attNames.Count -gt 0) {
                Write-Output ("        att: {0}" -f (($r.attNames | Select-Object -First 6) -join ', '))
            }
        }
    } else {
        $fail++
        Write-Output ("FAIL  {0}  (att={1} bones={2} flags=0x{3:X8})" -f (Split-Path $t -Leaf), $r.numatt, $r.boneCount, $r.flags)
        foreach ($e in $r.errors) { Write-Output "        $e" }
    }
}
Write-Output ""
Write-Output "==== $pass pass / $fail fail / $warned warn  ($($targets.Count) files, $withAtt carry attachments) ===="
if ($fail -gt 0) { exit 1 }
