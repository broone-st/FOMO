param([switch]$SelfTest)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$notePath = Join-Path $root 'notes.rtf'
$logPath  = Join-Path $root 'note_board.log'
function Write-Log($msg) {
    try { Add-Content -Path $logPath -Value ((Get-Date -Format 'HH:mm:ss') + ' ' + $msg) -Encoding UTF8 } catch { }
}

$f = New-Object System.Windows.Forms.Form
$f.Text = 'FOMO · 打字板'
$f.ClientSize = New-Object System.Drawing.Size(720, 470)
$f.StartPosition = 'CenterScreen'
$f.MinimumSize = New-Object System.Drawing.Size(520, 320)
$f.TopMost = $true

# ---------------- 工具栏 ----------------
$bar = New-Object System.Windows.Forms.Panel
$bar.Dock = 'Top'
$bar.Height = 84
$f.Controls.Add($bar)

function New-Btn($text, $x, $y, $w) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $text
    $b.Location = New-Object System.Drawing.Point($x, $y)
    $b.Size = New-Object System.Drawing.Size($w, 26)
    $bar.Controls.Add($b)
    return $b
}

$btnB    = New-Btn 'B' 8 6 34
$btnU    = New-Btn 'U' 46 6 34
$btnC    = New-Btn '颜色' 84 6 52
$btnI    = New-Btn '插入图片' 140 6 78
$btnShot = New-Btn '截屏' 224 6 62
$lblFind = New-Object System.Windows.Forms.Label
$lblFind.Text = '查找'
$lblFind.Location = New-Object System.Drawing.Point(300, 10)
$lblFind.Size = New-Object System.Drawing.Size(34, 20)
$bar.Controls.Add($lblFind)
$txtFind = New-Object System.Windows.Forms.TextBox
$txtFind.Location = New-Object System.Drawing.Point(338, 7)
$txtFind.Size = New-Object System.Drawing.Size(150, 22)
$bar.Controls.Add($txtFind)
$btnFind = New-Btn '查找下一个' 494 6 90
$btnSave = New-Btn '保存' 590 6 70

$lblA = New-Object System.Windows.Forms.Label
$lblA.Text = '透明度'
$lblA.Location = New-Object System.Drawing.Point(8, 44)
$lblA.Size = New-Object System.Drawing.Size(46, 18)
$bar.Controls.Add($lblA)
$tbA = New-Object System.Windows.Forms.TrackBar
$tbA.Minimum = 30; $tbA.Maximum = 100; $tbA.Value = 100
$tbA.TickFrequency = 10
$tbA.Location = New-Object System.Drawing.Point(56, 40)
$tbA.Size = New-Object System.Drawing.Size(120, 30)
$bar.Controls.Add($tbA)
$valA = New-Object System.Windows.Forms.Label
$valA.Text = '100%'
$valA.Location = New-Object System.Drawing.Point(178, 46)
$valA.Size = New-Object System.Drawing.Size(40, 18)
$bar.Controls.Add($valA)

$lblS = New-Object System.Windows.Forms.Label
$lblS.Text = '字号'
$lblS.Location = New-Object System.Drawing.Point(214, 44)
$lblS.Size = New-Object System.Drawing.Size(34, 18)
$bar.Controls.Add($lblS)
$tbS = New-Object System.Windows.Forms.TrackBar
$tbS.Minimum = 8; $tbS.Maximum = 48; $tbS.Value = 14
$tbS.TickFrequency = 4
$tbS.Location = New-Object System.Drawing.Point(246, 40)
$tbS.Size = New-Object System.Drawing.Size(120, 30)
$bar.Controls.Add($tbS)
$valS = New-Object System.Windows.Forms.Label
$valS.Text = '14'
$valS.Location = New-Object System.Drawing.Point(368, 46)
$valS.Size = New-Object System.Drawing.Size(34, 18)
$bar.Controls.Add($valS)

$lblI = New-Object System.Windows.Forms.Label
$lblI.Text = '图宽'
$lblI.Location = New-Object System.Drawing.Point(404, 44)
$lblI.Size = New-Object System.Drawing.Size(34, 18)
$bar.Controls.Add($lblI)
$tbI = New-Object System.Windows.Forms.TrackBar
$tbI.Minimum = 10; $tbI.Maximum = 100; $tbI.Value = 60
$tbI.TickFrequency = 10
$tbI.Location = New-Object System.Drawing.Point(436, 40)
$tbI.Size = New-Object System.Drawing.Size(110, 30)
$bar.Controls.Add($tbI)
$valI = New-Object System.Windows.Forms.Label
$valI.Text = '60%'
$valI.Location = New-Object System.Drawing.Point(548, 46)
$valI.Size = New-Object System.Drawing.Size(44, 18)
$bar.Controls.Add($valI)

# ---------------- 编辑区 ----------------
$rtb = New-Object System.Windows.Forms.RichTextBox
$rtb.Dock = 'Fill'
$rtb.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 14)
$rtb.WordWrap = $true
$rtb.ScrollBars = 'Vertical'
$rtb.AcceptsTab = $true
$f.Controls.Add($rtb)
$rtb.BringToFront()

# ---------------- 插图：缩放后走剪贴板 + Paste ----------------
function Insert-Image($img, [double]$scale) {
    if ($null -eq $img) { return $false }
    try {
        $w = [int]$img.Width
        $h = [int]$img.Height
        if ($w -le 0 -or $h -le 0) { return $false }
        if ($scale -le 0) { $scale = 1.0 }
        if ($scale -gt 1.0) { $scale = 1.0 }
        $nw = [int][Math]::Max(8, [Math]::Round($w * $scale))
        $nh = [int][Math]::Max(8, [Math]::Round($h * $scale))
        $bmp = New-Object System.Drawing.Bitmap($img, $nw, $nh)
        [System.Windows.Forms.Clipboard]::SetImage($bmp)
        $rtb.Paste()
        $rtb.AppendText("`r`n")
        return $true
    } catch {
        [System.Windows.Forms.MessageBox]::Show('插入图片失败：' + $_.Exception.Message, '打字板')
        return $false
    }
}

# ---------------- 截屏：全屏遮罩 + 拖框选区 ----------------
$script:selRect = $null
$script:ovStart = $null
$script:ovNow = $null

function Select-Region {
    $vb = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $ov = New-Object System.Windows.Forms.Form
    $ov.FormBorderStyle = 'None'
    $ov.StartPosition = 'Manual'
    $ov.Location = $vb.Location
    $ov.Size = $vb.Size
    $ov.TopMost = $true
    $ov.ShowInTaskbar = $false
    $ov.BackColor = [System.Drawing.Color]::Black
    $ov.Opacity = 0.35
    $ov.Cursor = [System.Windows.Forms.Cursors]::Cross
    $ov.KeyPreview = $true
    $script:ovStart = $null
    $script:ovNow = $null
    $script:selRect = $null

    $ov.add_MouseDown({
        $script:ovStart = $_.Location
        $script:ovNow = $_.Location
        $ov.Invalidate()
    })
    $ov.add_MouseMove({
        if ($null -ne $script:ovStart) {
            $script:ovNow = $_.Location
            $ov.Invalidate()
        }
    })
    $ov.add_Paint({
        if ($null -ne $script:ovStart -and $null -ne $script:ovNow) {
            $x = [Math]::Min($script:ovStart.X, $script:ovNow.X)
            $y = [Math]::Min($script:ovStart.Y, $script:ovNow.Y)
            $w = [Math]::Abs($script:ovNow.X - $script:ovStart.X)
            $h = [Math]::Abs($script:ovNow.Y - $script:ovStart.Y)
            if ($w -gt 0 -and $h -gt 0) {
                $pen = New-Object System.Drawing.Pen([System.Drawing.Color]::DeepSkyBlue, 2)
                $_.Graphics.DrawRectangle($pen, $x, $y, $w, $h)
                $pen.Dispose()
                $txt = "$w x $h"
                $br = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::DeepSkyBlue)
                $_.Graphics.DrawString($txt, (New-Object System.Drawing.Font('Segoe UI', 11)), $br, ($x + 6), ($y + 4))
                $br.Dispose()
            }
        }
    })
    $ov.add_MouseUp({
        if ($null -ne $script:ovStart -and $null -ne $script:ovNow) {
            $x = [Math]::Min($script:ovStart.X, $script:ovNow.X)
            $y = [Math]::Min($script:ovStart.Y, $script:ovNow.Y)
            $w = [Math]::Abs($script:ovNow.X - $script:ovStart.X)
            $h = [Math]::Abs($script:ovNow.Y - $script:ovStart.Y)
            if ($w -ge 5 -and $h -ge 5) {
                $script:selRect = New-Object System.Drawing.Rectangle($x, $y, $w, $h)
            }
        }
        $ov.Close()
    })
    $ov.add_KeyDown({
        if ($_.KeyCode -eq 'Escape') { $script:selRect = $null; $ov.Close() }
    })

    Write-Log ('shot: overlay ' + $ov.Width + 'x' + $ov.Height + ' at ' + $ov.Location)
    [void]$ov.ShowDialog()
    $ov.Dispose()
    return $script:selRect
}

$btnShot.add_Click({
    Write-Log 'shot: button clicked'
    try {
        $r = Select-Region
        Write-Log ('shot: rect=' + $r)
    } catch {
        Write-Log ('shot ERROR: ' + $_.Exception.Message)
        return
    }
    if ($null -eq $r) { return }
    if ($r.Width -lt 5 -or $r.Height -lt 5) { return }
    try {
        $vb = [System.Windows.Forms.SystemInformation]::VirtualScreen
        $bmp = New-Object System.Drawing.Bitmap($r.Width, $r.Height)
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($vb.X + $r.X, $vb.Y + $r.Y, 0, 0, $bmp.Size)
        $g.Dispose()
        $maxW = [Math]::Max(120, $rtb.ClientSize.Width - 24)
        $sc = [Math]::Min(1.0, $maxW / [double]$bmp.Width)
        $ok = Insert-Image $bmp $sc
        Write-Log ('shot: inserted=' + $ok + ' size=' + $bmp.Width + 'x' + $bmp.Height + ' scale=' + [Math]::Round($sc,2))
        $bmp.Dispose()
    } catch {
        [System.Windows.Forms.MessageBox]::Show('截屏失败：' + $_.Exception.Message, '打字板')
    }
})

$btnB.add_Click({
    if ($rtb.SelectionFont -ne $null) {
        $st = $rtb.SelectionFont.Style
        if ($st -band [System.Drawing.FontStyle]::Bold) { $st = $st -bxor [System.Drawing.FontStyle]::Bold }
        else { $st = $st -bor [System.Drawing.FontStyle]::Bold }
        $rtb.SelectionFont = New-Object System.Drawing.Font($rtb.SelectionFont, $st)
    }
})

$btnU.add_Click({
    if ($rtb.SelectionFont -ne $null) {
        $st = $rtb.SelectionFont.Style
        if ($st -band [System.Drawing.FontStyle]::Underline) { $st = $st -bxor [System.Drawing.FontStyle]::Underline }
        else { $st = $st -bor [System.Drawing.FontStyle]::Underline }
        $rtb.SelectionFont = New-Object System.Drawing.Font($rtb.SelectionFont, $st)
    }
})

$btnC.add_Click({
    $dlg = New-Object System.Windows.Forms.ColorDialog
    if ($dlg.ShowDialog() -eq 'OK') { $rtb.SelectionColor = $dlg.Color }
})

$tbA.add_ValueChanged({
    $v = $tbA.Value
    $valA.Text = "$v%"
    if ($v -lt 10) { $v = 10 }
    $f.Opacity = $v / 100.0
})

$tbS.add_ValueChanged({
    $valS.Text = '' + $tbS.Value
    if ($rtb.SelectionFont -ne $null) {
        $rtb.SelectionFont = New-Object System.Drawing.Font($rtb.SelectionFont.FontFamily, [float]$tbS.Value, $rtb.SelectionFont.Style)
    }
})

$tbI.add_ValueChanged({ $valI.Text = '' + $tbI.Value + '%' })

$btnFind.add_Click({
    $needle = $txtFind.Text
    if ($needle.Length -eq 0) { return }
    $start = $rtb.SelectionStart + $rtb.SelectionLength
    $idx = $rtb.Find($needle, $start, [System.Windows.Forms.RichTextBoxFinds]::None)
    if ($idx -lt 0) { $idx = $rtb.Find($needle, 0, [System.Windows.Forms.RichTextBoxFinds]::None) }
    if ($idx -ge 0) {
        $rtb.Select($idx, $needle.Length)
        $rtb.ScrollToCaret()
        $rtb.Focus()
    } else {
        [System.Windows.Forms.MessageBox]::Show('没有找到：' + $needle, '查找')
    }
})

$txtFind.add_KeyDown({
    if ($_.KeyCode -eq 'Enter') { $btnFind.PerformClick(); $_.SuppressKeyPress = $true }
})

$btnI.add_Click({
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = '图片|*.png;*.jpg;*.jpeg;*.bmp;*.gif'
    if ($dlg.ShowDialog() -eq 'OK') {
        try {
            $img = [System.Drawing.Image]::FromFile($dlg.FileName)
            [void](Insert-Image $img ($tbI.Value / 100.0))
            $img.Dispose()
        } catch {
            [System.Windows.Forms.MessageBox]::Show('读取图片失败：' + $_.Exception.Message, '打字板')
        }
    }
})

$rtb.add_KeyDown({
    if ($_.Control -and $_.KeyCode -eq 'V') {
        if ([System.Windows.Forms.Clipboard]::ContainsImage()) {
            $_.SuppressKeyPress = $true
            $img = [System.Windows.Forms.Clipboard]::GetImage()
            [void](Insert-Image $img ($tbI.Value / 100.0))
            $img.Dispose()
        }
    }
})

$btnSave.add_Click({
    try {
        $rtb.SaveFile($notePath, [System.Windows.Forms.RichTextBoxStreamType]::RichText)
        $f.Text = 'FOMO · 打字板（已保存）'
    } catch {
        [System.Windows.Forms.MessageBox]::Show('保存失败：' + $_.Exception.Message, '打字板')
    }
})

$f.add_FormClosing({
    try { $rtb.SaveFile($notePath, [System.Windows.Forms.RichTextBoxStreamType]::RichText) } catch { }
})

if (Test-Path $notePath) {
    try { $rtb.LoadFile($notePath, [System.Windows.Forms.RichTextBoxStreamType]::RichText) } catch { }
}

# ---------------- 自检（自动化验证用；正常使用不会进入） ----------------
if ($SelfTest) {
    $null = $rtb.Handle
    $rtb.Clear()
    $t = New-Object System.Drawing.Bitmap(240, 140)
    $g2 = [System.Drawing.Graphics]::FromImage($t)
    $g2.Clear([System.Drawing.Color]::LightSkyBlue)
    $g2.FillEllipse([System.Drawing.Brushes]::Tomato, 20, 20, 100, 90)
    $g2.Dispose()
    $ok = Insert-Image $t 0.6
    $t.Dispose()
    $rtfLen = 0; $hasPict = $false
    try { $rtfLen = $rtb.Rtf.Length; $hasPict = $rtb.Rtf.Contains('\pict') } catch { }
    Write-Output ("SELFTEST ok={0} rtfLen={1} hasPict={2} shotBtn={3}" -f $ok, $rtfLen, $hasPict, $btnShot.Text)
    try { $rtb.SaveFile((Join-Path $root 'selftest.rtf'), [System.Windows.Forms.RichTextBoxStreamType]::RichText) } catch { }
    exit 0
}

[System.Windows.Forms.Application]::Run($f)
