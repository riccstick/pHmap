"""A small coordinate-bearing mmCIF response for offline acquisition tests."""

from io import BytesIO

CIF = b"""data_test
#
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . ALA A 1 1 ? 0.0 0.0 0.0 1.0 10.0 1 ALA A N 1
#
"""


class StructureResponse(BytesIO):
    def __init__(self, data: bytes = CIF, *, length: str | None = None) -> None:
        super().__init__(data)
        self.headers = {} if length is None else {"Content-Length": length}
