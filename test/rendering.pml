
load 5.0-p2p.pqr.dx
load 5.0-p2p.pqr
as surface
ramp_new ramp, 5.0-p2p.pqr, [-5, 0, 5], [red, white, blue]
set surface_color, ramp
set surface_ramp_above_mode
disable ramp
set_view (\
    -0.504395962,   -0.693623662,    0.514263749,\
    -0.791490853,    0.133337766,   -0.596460760,\
     0.345149219,   -0.707887590,   -0.616252184,\
     0.000000000,    0.000000000, -229.575546265,\
   152.980361938,   30.766546249,  173.040679932,\
   180.999114990,  278.151977539,  -20.000000000 )
center
zoom
set opaque_background, off
bg_color None
png 5.0-pic.png, width=500, height=500, dpi=300