from __future__ import annotations

from src.services.pubchem import PubChemClient


def test_pubchem_parse_full_payload() -> None:
    raw = {
        "PropertyTable": {
            "Properties": [
                {
                    "CID": 753,
                    "MolecularWeight": "92.09",
                    "IUPACName": "propane-1,2,3-triol",
                    "XLogP": -1.8,
                    "HBondDonorCount": 3,
                    "HBondAcceptorCount": 3,
                }
            ]
        }
    }
    info = PubChemClient._parse("753", raw)
    assert info is not None
    assert info.cid == "753"
    assert info.iupac_name == "propane-1,2,3-triol"
    assert info.molecular_weight == 92.09
    assert info.xlogp == -1.8
    assert info.h_bond_donor_count == 3
    assert info.h_bond_acceptor_count == 3


def test_pubchem_parse_missing_optional_fields() -> None:
    raw = {"PropertyTable": {"Properties": [{"CID": 1}]}}
    info = PubChemClient._parse("1", raw)
    assert info is not None
    assert info.iupac_name is None
    assert info.molecular_weight is None
    assert info.xlogp is None
    assert info.h_bond_donor_count is None


def test_pubchem_parse_garbage_payload() -> None:
    assert PubChemClient._parse("1", {}) is None
    assert PubChemClient._parse("1", {"PropertyTable": {}}) is None
    assert PubChemClient._parse("1", {"PropertyTable": {"Properties": []}}) is None


def test_pubchem_parse_string_numbers_coerced() -> None:
    raw = {
        "PropertyTable": {
            "Properties": [
                {
                    "MolecularWeight": "204.35",
                    "XLogP": "5.2",
                    "HBondDonorCount": "0",
                }
            ]
        }
    }
    info = PubChemClient._parse("99", raw)
    assert info is not None
    assert info.molecular_weight == 204.35
    assert info.xlogp == 5.2
    assert info.h_bond_donor_count == 0


def test_pubchem_parse_invalid_numbers_become_none() -> None:
    raw = {
        "PropertyTable": {
            "Properties": [
                {"MolecularWeight": "n/a", "XLogP": "?"}
            ]
        }
    }
    info = PubChemClient._parse("1", raw)
    assert info is not None
    assert info.molecular_weight is None
    assert info.xlogp is None
