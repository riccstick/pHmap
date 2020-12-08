
load /home/ricci/Github/pHmap/example/p2pFiles/mutant1-4.5-p2p.pqr.dx
load /home/ricci/Github/pHmap/example/p2pFiles/mutant1-4.5-p2p.pqr
as surface
show sticks, hetatm
ramp_new ramp, mutant1-4.5-p2p.pqr, [-5, 0, 5], [red, white, blue]
set surface_color, ramp
set surface_ramp_above_mode
disable ramp
set_view (\
    -0.583356678,   -0.747116983,    0.318607748,\
    -0.694660485,    0.255667239,   -0.672370017,\
     0.420881391,   -0.613555789,   -0.668137789,\
     0.000000000,    0.000000000, -270.598022461,\
   149.767730713,   27.725906372,  172.819793701,\
   213.341552734,  327.854492188,  -20.000000000 )
center
zoom
set opaque_background, off
bg_color None
png mutant1-4.5-pic.png, width=1000, height=1000, dpi=300
hide everything
enable ramp
png rawRamp.png, width=1000, height=1000, dpi=300