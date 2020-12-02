
load protein-7.0-p2p.pqr.dx
load protein-7.0-p2p.pqr
as surface
show sticks, hetatm
ramp_new ramp, protein-7.0-p2p.pqr, [-5, 0, 5], [red, white, blue]
set surface_color, ramp
set surface_ramp_above_mode
disable ramp
 
center
zoom
set opaque_background, off
bg_color None
png protein-7.0-pic.png, width=500, height=500, dpi=300
hide everything
enable ramp
png rawRamp.png, width=500, height=500, dpi=300