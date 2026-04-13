param(
    [Parameter(Mandatory = $true)]
    [string]$ImagePath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Convert-BoundsToPolygon {
    param(
        [double]$MinX,
        [double]$MinY,
        [double]$MaxX,
        [double]$MaxY
    )

    $x = [int][Math]::Round($MinX)
    $y = [int][Math]::Round($MinY)
    $w = [int][Math]::Round($MaxX - $MinX)
    $h = [int][Math]::Round($MaxY - $MinY)

    $topLeft = @($x, $y)
    $topRight = @(($x + $w), $y)
    $bottomRight = @(($x + $w), ($y + $h))
    $bottomLeft = @($x, ($y + $h))

    return @(
        $topLeft,
        $topRight,
        $bottomRight,
        $bottomLeft
    )
}

function Await-WinRtOperation {
    param(
        [Parameter(Mandatory = $true)]
        $Operation,
        [Parameter(Mandatory = $true)]
        [Type]$ResultType
    )

    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object {
            $_.Name -eq 'AsTask' -and
            $_.IsGenericMethodDefinition -and
            $_.GetGenericArguments().Count -eq 1 -and
            $_.GetParameters().Count -eq 1
        } |
        Select-Object -First 1

    if ($null -eq $method) {
        throw "Unable to locate System.WindowsRuntimeSystemExtensions.AsTask<TResult>."
    }

    $generic = $method.MakeGenericMethod($ResultType)
    $task = $generic.Invoke($null, @($Operation))
    return $task.Result
}

try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    [void][Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Graphics.Imaging.SoftwareBitmap, Windows.Foundation, ContentType = WindowsRuntime]
    [void][Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]

    $fileOp = [Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)
    $file = Await-WinRtOperation -Operation $fileOp -ResultType ([Windows.Storage.StorageFile])

    $streamOp = $file.OpenAsync([Windows.Storage.FileAccessMode]::Read)
    $stream = Await-WinRtOperation -Operation $streamOp -ResultType ([Windows.Storage.Streams.IRandomAccessStream])

    $decoderOp = [Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)
    $decoder = Await-WinRtOperation -Operation $decoderOp -ResultType ([Windows.Graphics.Imaging.BitmapDecoder])

    $bitmapOp = $decoder.GetSoftwareBitmapAsync()
    $bitmap = Await-WinRtOperation -Operation $bitmapOp -ResultType ([Windows.Graphics.Imaging.SoftwareBitmap])

    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if ($null -eq $engine) {
        throw "Windows Media OCR engine is unavailable for the current user profile languages."
    }

    $ocrOp = $engine.RecognizeAsync($bitmap)
    $ocrResult = Await-WinRtOperation -Operation $ocrOp -ResultType ([Windows.Media.Ocr.OcrResult])

    $lines = @()
    foreach ($line in $ocrResult.Lines) {
        $words = @($line.Words)
        if ($words.Count -eq 0) {
            $lines += @{
                text = $line.Text
                score = 1.0
                bbox = @()
            }
            continue
        }

        $xs = @()
        $ys = @()
        $x2s = @()
        $y2s = @()
        foreach ($word in $words) {
            $wordRect = $word.BoundingRect
            $xs += [double]$wordRect.X
            $ys += [double]$wordRect.Y
            $x2s += [double]($wordRect.X + $wordRect.Width)
            $y2s += [double]($wordRect.Y + $wordRect.Height)
        }

        $minX = ($xs | Measure-Object -Minimum).Minimum
        $minY = ($ys | Measure-Object -Minimum).Minimum
        $maxX = ($x2s | Measure-Object -Maximum).Maximum
        $maxY = ($y2s | Measure-Object -Maximum).Maximum

        $lines += @{
            text = $line.Text
            score = 1.0
            bbox = Convert-BoundsToPolygon -MinX $minX -MinY $minY -MaxX $maxX -MaxY $maxY
        }
    }

    @{
        ok = $true
        engine = "windows_media_ocr"
        lines = $lines
        message = "OCR completed with $($lines.Count) text lines."
    } | ConvertTo-Json -Depth 8 -Compress
}
catch {
    @{
        ok = $false
        engine = "windows_media_ocr"
        lines = @()
        message = $_.Exception.Message
    } | ConvertTo-Json -Depth 8 -Compress
}
