"""Fauxel OpenBIM connectors: BCF 2.1/3.0 and Speckle."""
from .bcf import BcfError, build_bcf21_zip, build_bcf30_zip, convert, kind_of, note_of, parse_bcf, severity_of

__version__ = "0.1.0"
