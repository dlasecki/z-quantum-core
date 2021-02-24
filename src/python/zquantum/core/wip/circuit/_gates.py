"""Class hierarchy for base gates."""
import math
from dataclasses import dataclass
import typing as t

import sympy


@dataclass(frozen=True)
class GateApplication:
    gate: "Gate"
    qubit_indices: t.Iterable[int]


@dataclass(frozen=True)
class Gate:
    """Quantum gate defined with a matrix.

    Args:
        name: Name of this gate. Implementers of new gates should make sure that the names are
            unique.
        matrix: Unitary matrix defining action of this gate.
    """

    name: str
    matrix: sympy.Matrix

    def __call__(self, *qubit_indices) -> "GateApplication":
        return GateApplication(self, qubit_indices)


def _n_qubits_for_matrix(matrix_shape):
    n_qubits = math.floor(math.log2(matrix_shape[0]))
    if 2 ** n_qubits != matrix_shape[0] or 2 ** n_qubits != matrix_shape[1]:
        raise ValueError("Gate's matrix has to be square with dimension 2^N")

    return n_qubits


def make_parametric_gate_factory(
    name: str,
    matrix_factory
):
    def _gate_factory(*params):
        return Gate(
            name=name,
            matrix=matrix_factory(*params)
        )

    return _gate_factory
