
load protein-5.0-p2p.pqr.dx
load protein-5.0-p2p.pqr
as surface
show sticks, hetatm
ramp_new ramp, protein-5.0-p2p.pqr, [-5, 0, 5], [red, white, blue]
set surface_color, ramp
set surface_ramp_above_mode
disable ramp
 
center
zoom
set opaque_background, off
bg_color None
png protein-5.0-pic.png, width=1000, height=1000, dpi=300
hide everything
enable ramp
png rawRamp.png, width=1000, height=1000, dpi=300