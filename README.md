# pHmap
Software for generating a surface charge based pH-profile map.

<img src="./pH_profile+label.png" alt="pH_profile" style="zoom:50%;" />

## Requirements
- [PDB2PQR](http://www.poissonboltzmann.org/) - for calculation of protonation states
- [ABPS](http://www.poissonboltzmann.org/) - for calculation of electrostatic surface
- [PyMol](https://github.com/schrodinger/pymol-open-source) - for generating surface visualization and .pngs
- [ImageMagick](https://imagemagick.org/) - for generating final multi .png picture
- [Argbash](https://argbash.readthedocs.io/en/latest/index.html) (argument parser generator)

## Installation

- Developed under Ubuntu_20.04.1 LTS

- Bash script, just make it executable and run

### SourceCode compiling

- Template file can be changed and converted to executable code (tested with argbash_2.10)

  `argbash pHmap.template -o pHmap`

 - Completion file add to `/etc/bash_completion.d/`

  `argbash pHmap.template --type completion --strip all -o pHmap.m4`

- Required programs have to be in executable paths

### PDB2PQR

- Download from Github: [pdb2pqr_2.1.1](https://github.com/Electrostatics/pdb2pqr/releases/tag/v2.1.1)  for Linux [pdb2pqr.zip](https://github.com/Electrostatics/pdb2pqr/releases/download/v2.1.1/pdb2pqr-linux-bin64-2.1.1.tar.gz)
- [Install](https://erikbreslmayr.medium.com/installing-pre-compiled-apbs-for-electrostatic-surface-and-pdb2pqr-for-protonation-state-15fd068574b9)

### ABPS

- Download from Github: [ABTS_v3.0.0](https://github.com/Electrostatics/apbs/releases/tag/v3.0.0) for Linux [apbs.zip](https://github.com/Electrostatics/apbs/releases/download/v3.0.0/APBS-3.0.0_Linux.zip)
- [Install](https://erikbreslmayr.medium.com/installing-pre-compiled-apbs-for-electrostatic-surface-and-pdb2pqr-for-protonation-state-15fd068574b9)

### PyMol

- Open Source PyMol v2.3

> `sudo apt install pymol`

### ImageMagick

- ImageMagick 6.9.7-4 Q16 x86_64 20170114

> `sudo apt-get install imagemagick imagemagick-doc` 
- [Commands](https://imagemagick.org/script/command-line-options.php#fill)
