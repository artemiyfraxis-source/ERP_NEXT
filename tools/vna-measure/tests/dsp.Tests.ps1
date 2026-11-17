$here=Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here '..\dsp.ps1')

function New-DspTestData {
    param([double[]]$Frequencies=@(1e7,1e9,18e9),[double]$Reflection=0.01,[double]$Transmission=0.95)
    $data=@{ Frequencies=$Frequencies }
    foreach ($name in 'S11','S21','S12','S22') {
        $values=New-Object 'System.Numerics.Complex[]' $Frequencies.Count
        for ($index=0; $index -lt $Frequencies.Count; $index++) {
            if ($name -in 'S11','S22') { $values[$index]=[System.Numerics.Complex]::new($Reflection,0.001*$index) }
            else { $values[$index]=[System.Numerics.Complex]::FromPolarCoordinates($Transmission,-0.2*$index) }
        }
        $data[$name]=$values
    }
    return (New-VnaData @data)
}

Describe 'Touchstone numerical data' {
    It 'roundtrips complex RI data with invariant decimals and Russian comments' {
        $data=New-DspTestData
        $path=Join-Path $TestDrive 'roundtrip.s2p'
        $oldCulture=[Threading.Thread]::CurrentThread.CurrentCulture
        try {
            [Threading.Thread]::CurrentThread.CurrentCulture=[Globalization.CultureInfo]::GetCultureInfo('ru-RU')
            Write-VnaTouchstone -Path $path -Data $data -Comments @('СИ.001, АКМ','line one' + "`n# harmless comment")
            $actual=Read-VnaTouchstone $path
        } finally { [Threading.Thread]::CurrentThread.CurrentCulture=$oldCulture }
        $actual.Frequencies.Count | Should Be 3
        $actual.Frequencies[2] | Should Be 18e9
        foreach ($name in 'S11','S21','S12','S22') {
            for ($index=0; $index -lt 3; $index++) { ($actual.$name[$index]-$data.$name[$index]).Magnitude | Should BeLessThan 1e-14 }
        }
        [IO.File]::ReadAllText($path).Contains('СИ.001') | Should Be $true
    }
    It 'reads MA, GHz units, continued records and inline comments' {
        $path=Join-Path $TestDrive 'ma.s2p'
        @('# GHZ S MA R 50','! fixture','1 0.1 90 0.5 -90 ! first part','0.5 -90 0.2 180') | Set-Content $path
        $actual=Read-VnaTouchstone $path
        $actual.Frequencies[0] | Should Be 1e9
        [math]::Abs($actual.S11[0].Imaginary-0.1) | Should BeLessThan 1e-14
        [math]::Abs($actual.S22[0].Real+0.2) | Should BeLessThan 1e-14
    }
    It 'reads DB, MHz units and Fortran exponent numbers' {
        $path=Join-Path $TestDrive 'db.s2p'
        @('# MHZ S DB R 50','1D1 -20 0 -6.020599913279624 0 -6.020599913279624 0 -20 0') | Set-Content $path
        $actual=Read-VnaTouchstone $path
        $actual.Frequencies[0] | Should Be 1e7
        [math]::Abs($actual.S21[0].Real-0.5) | Should BeLessThan 1e-14
    }
    It 'rejects 75 ohm fixtures' {
        $path=Join-Path $TestDrive '75.s2p'
        @('# HZ S RI R 75','10 0 0 1 0 1 0 0 0') | Set-Content $path
        { Read-VnaTouchstone $path } | Should Throw
    }
    It 'rejects missing header, partial records, NaN and descending grids' {
        $path=Join-Path $TestDrive 'invalid.s2p'
        foreach ($content in @('10 0 0 1 0 1 0 0 0',"# HZ S RI R 50`n10 0 0", "# HZ S RI R 50`n10 NaN 0 1 0 1 0 0 0", "# HZ S RI R 50`n10 0 0 1 0 1 0 0 0`n9 0 0 1 0 1 0 0 0")) {
            $content | Set-Content $path
            { Read-VnaTouchstone $path } | Should Throw
        }
    }
    It 'refuses invalid arrays and infinite complex values before writing' {
        $data=New-DspTestData
        $data.S21[0]=[System.Numerics.Complex]::new([double]::PositiveInfinity,0)
        { Write-VnaTouchstone (Join-Path $TestDrive 'bad.s2p') $data } | Should Throw
        $data=New-DspTestData
        $data.S22=@()
        { Assert-VnaData $data } | Should Throw
    }
}

Describe 'T-matrix fixture removal' {
    It 'recovers known DUT from two distinct nonreciprocal fixtures within 1e-9' {
        $dut=New-DspTestData -Reflection 0.035 -Transmission 0.82
        $left=New-DspTestData -Reflection 0.07 -Transmission 0.93
        $right=New-DspTestData -Reflection 0.09 -Transmission 0.88
        $left.S12[1]=[System.Numerics.Complex]::new(0.87,-0.12)
        $right.S22[2]=[System.Numerics.Complex]::new(0.06,0.03)
        $measured=New-DspTestData
        for ($index=0; $index -lt 3; $index++) {
            $leftT=ConvertTo-VnaTMatrix $left.S11[$index] $left.S21[$index] $left.S12[$index] $left.S22[$index]
            $dutT=ConvertTo-VnaTMatrix $dut.S11[$index] $dut.S21[$index] $dut.S12[$index] $dut.S22[$index]
            $rightT=ConvertTo-VnaTMatrix $right.S11[$index] $right.S21[$index] $right.S12[$index] $right.S22[$index]
            $cascade=Multiply-VnaTMatrix (Multiply-VnaTMatrix $leftT $dutT) $rightT
            $parameters=ConvertFrom-VnaTMatrix $cascade
            $measured.S11[$index]=$parameters[0]; $measured.S21[$index]=$parameters[1]
            $measured.S12[$index]=$parameters[2]; $measured.S22[$index]=$parameters[3]
        }
        $actual=Remove-VnaFixtures -Data $measured -Left $left -Right $right
        foreach ($name in 'S11','S21','S12','S22') {
            for ($index=0; $index -lt 3; $index++) { ($actual.$name[$index]-$dut.$name[$index]).Magnitude | Should BeLessThan 1e-9 }
        }
    }
    It 'preserves direct data including a zero transmission without T conversion' {
        $data=New-DspTestData
        $data.S21[0]=[System.Numerics.Complex]::Zero
        $actual=Remove-VnaFixtures $data
        $actual.S21[0].Magnitude | Should Be 0
        $actual.S11[2].Real | Should Be $data.S11[2].Real
    }
    It 'refuses a different fixture grid even when point counts match' {
        $data=New-DspTestData
        $fixture=New-DspTestData -Frequencies @(1e7,1.1e9,18e9)
        { Remove-VnaFixtures -Data $data -Left $fixture } | Should Throw
    }
    It 'refuses short fixture grids and singular inverse matrices' {
        $data=New-DspTestData
        $fixture=New-DspTestData -Frequencies @(1e7,1e9)
        { Remove-VnaFixtures -Data $data -Right $fixture } | Should Throw
        { Invert-VnaTMatrix ([System.Numerics.Complex[]]@(1,1,1,1)) } | Should Throw
        { ConvertTo-VnaTMatrix 0 0 0 0 } | Should Throw
    }
}

Describe 'Band and summary' {
    It 'crops to measured points within requested band and preserves a single point array' {
        $data=New-DspTestData
        $actual=Select-VnaBand -Data $data -FmaxHz 11e9
        $actual.Frequencies.Count | Should Be 2
        $actual.Frequencies[-1] | Should Be 1e9
        $single=Select-VnaBand -Data $data -FmaxHz 1e7
        $single.Frequencies.Count | Should Be 1
        $single.S22.Count | Should Be 1
        { Select-VnaBand -Data $data -FmaxHz 1 } | Should Throw
        { Select-VnaBand -Data $data -FmaxHz ([double]::NaN) } | Should Throw
    }
    It 'reports actual band, max VSWR on each side and insertion loss' {
        $data=New-DspTestData -Reflection 0.1 -Transmission 0.5
        $data.S11[2]=[System.Numerics.Complex]::new(0.2,0)
        $summary=Get-VnaSummary $data
        [math]::Abs($summary.Vswr1-1.5) | Should BeLessThan 1e-12
        ($summary.Vswr2 -gt 1.22) | Should Be $true
        [math]::Abs($summary.LossDb-6.020599913279624) | Should BeLessThan 1e-12
        $summary.FmaxHz | Should Be 18e9
    }
    It 'reports infinite VSWR for unit reflection and infinite loss for no transmission' {
        $data=New-DspTestData
        $data.S11[0]=[System.Numerics.Complex]::One
        $data.S21[0]=[System.Numerics.Complex]::Zero
        $summary=Get-VnaSummary $data
        [double]::IsPositiveInfinity($summary.Vswr1) | Should Be $true
        [double]::IsPositiveInfinity($summary.LossDb) | Should Be $true
    }
}
