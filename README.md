# pHmap
Software for generating a surface charge based pH-profile map.

## Requirements
- [PDB2PQR](http://www.poissonboltzmann.org/) - for calculation of protonation states
- [ABPS](http://www.poissonboltzmann.org/) - for calculation of electrostatic surface
- [PyMol](https://github.com/schrodinger/pymol-open-source) - for generating surface visualization and .pngs
- [ImageMagick](https://imagemagick.org/) - for generating final multi .png picture

## Installation

- Developed under Ubuntu_20.04.1 LTS
- Bast scripts - make executable and run
- Required programs have to be in executable paths

## PDB2PQR

- Download from Github: [pdb2pqr_2.1.1](https://github.com/Electrostatics/pdb2pqr/releases/tag/v2.1.1)  for [Linux](https://github.com/Electrostatics/pdb2pqr/releases/download/v2.1.1/pdb2pqr-linux-bin64-2.1.1.tar.gz)

## ABPS

- Download from Github: [ABTS_v3.0.0](https://github.com/Electrostatics/apbs/releases/tag/v3.0.0) for [Linux](https://github.com/Electrostatics/apbs/releases/download/v3.0.0/APBS-3.0.0_Linux.zip)

## PyMol

- Open Source PyMol v2.3

> `sudo apt install pymol`

## ImageMagick

> `sudo apt-get install imagemagick imagemagick-doc` 

# Settings

### PDB2OPQR

pdb2pqr --with-ph=6.0 --ph-calc-method=propka --drop-water --apbs-input --ff=parse --verbose --ligand=FAD.mol2 NcCDHDH.pdb out.pqr



### APBS

- apbs --output-file=apbs.log temp.in