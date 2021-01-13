# """Tools for constructing quantum circuits."""
import json
import numpy as np
import pyquil
import cirq
import qiskit
import random
import warnings

from qiskit import QuantumRegister

from pyquil import Program
from pyquil.gates import *
from pyquil.quilatom import quil_cos, quil_sin

from math import pi
from ..utils import convert_array_to_dict, convert_dict_to_array
from ._gate import Gate
from ._qubit import Qubit
from ._gateset import COMMON_GATES, UNIQUE_GATES
from ..utils import SCHEMA_VERSION, pauli_x, pauli_y, pauli_z, identity
from openfermion.ops import FermionOperator


class Circuit(object):
    """Base class for quantum circuits.

    Attributes:
        name: string
            Name of the Circuit object. By default this is called 'Unnamed'.
        gates: list[Gate]
            The gate sequence of the circuit. Implemented as a list of core.gate.Gate
            objects.
        qubits: list[Qubit]
            The set of qubits that the circuit acts on. Implemented as a list of
            core.qubit.Qubit objects.
        info: dictionary
            Additional information related to the circuit. For example if the circuit is converted
            from another package, infomation related to the native specification of the circuit in
            that package is recorded here.
    """

    def __init__(self, input_object=None, name="Unnamed"):
        """Initialize a circuit. Most likely the circuit is generated by converting a circuit
        object in other packages to core.circuit.Circuit object.

        Args:
            input_object: pyquil.Program, cirq.Circuit, qiskit.QuantumCircuit
                A generic circuit object that may be created from one of the various packages
                currently supported by Zap OS.
        """

        self.name = name  # name of the circuit (by default a random Hash string)
        self.gates = []  # list of gates (see gate.py for Gate class def)
        self.qubits = []  # list of qubits (see qubit.py for Qubit class def)
        self.info = {
            "label": None  # the name of the native package that generates the circuit
            # e.g. 'pyquil', 'cirq', 'qiskit' etc. The purpose is to
            # provide hints about what unique functionalities of
            # the package one might be able to take advantage of.
        }

        if isinstance(input_object, pyquil.Program):
            self.from_pyquil(input_object)
        elif isinstance(input_object, pyquil.quilbase.Gate):
            converted_input = pyquil.Program(input_object)
            self.from_pyquil(converted_input)
        elif isinstance(input_object, cirq.Circuit):
            self.from_cirq(input_object)
        elif isinstance(input_object, qiskit.QuantumCircuit):
            self.from_qiskit(input_object)
        elif input_object is None:
            pass
        else:
            raise (
                TypeError(
                    "Incorrect type of input object: {0}".format(type(input_object))
                )
            )

    @property
    def n_multiqubit_gates(self):
        """The number of multiqubit gates in the circuit."""

        n_mq_gates = 0
        for gate in self.gates:
            if len(gate.qubits) > 1:
                n_mq_gates += 1

        return n_mq_gates

    @property
    def symbolic_params(self):
        """
        Returns a set of symbolic parameters used in the circuit in the chronological order.

        Returns:
            list: list of all the sympy symbols used as params of gates in the circuit.
        """
        symbolic_params = []
        for gate in self.gates:
            symbolic_params_per_gate = gate.symbolic_params
            for param in symbolic_params_per_gate:
                if param not in symbolic_params:
                    symbolic_params.append(param)

        return symbolic_params

    def __eq__(self, anotherCircuit):
        """Comparison between two Circuit objects."""
        if self.name != anotherCircuit.name:
            return False
        if len(self.qubits) != len(anotherCircuit.qubits):
            return False
        for i in range(len(self.qubits)):
            if str(self.qubits[i]) != str(anotherCircuit.qubits[i]):
                return False

        if len(self.gates) != len(anotherCircuit.gates):
            return False
        for i in range(len(self.gates)):
            if self.gates[i] != anotherCircuit.gates[i]:
                return False

        return True

    def __add__(self, other_circuit):
        """Add two circuits."""

        qubit_indices = set(
            [qubit.index for qubit in self.qubits]
            + [qubit.index for qubit in other_circuit.qubits]
        )

        new_circuit = Circuit()

        for qubit_index in qubit_indices:
            new_circuit.qubits.append(Qubit(qubit_index))

        new_circuit.gates = self.gates + other_circuit.gates

        return new_circuit

    def get_qubits(self):
        """Returns a list of qubit indices (ints)."""

        return [q.index for q in self.qubits]

    def evaluate(self, symbols_map):
        """
        Returns a copy of a circuit with specified symbolic parameters evaluated to provided values.

        Args:
            symbols_map list(tuple(sympy.Basic, number)): List containing symbols and values that they should take.
        """
        new_circuit = type(self)()
        new_circuit.name = self.name
        new_circuit.qubits = self.qubits
        new_circuit.info = self.info
        gates = []

        all_symbols_in_map = set([item[0] for item in symbols_map])
        if len(all_symbols_in_map - set(self.symbolic_params)) > 0:
            warnings.warn(
                """
                Trying to evaluate circuit with symbols not existing in the circuit:
                Symbols in circuit: {0}
                Symbols in the map: {1}
                """.format(
                    self.symbolic_params, all_symbols_in_map
                ),
                Warning,
            )

        for gate in self.gates:
            gates.append(gate.evaluate(symbols_map))

        new_circuit.gates = gates
        return new_circuit

    def to_pyquil(self):
        """Converts the circuit to a pyquil Program object."""

        output = Program()
        if self.gates != None:
            for gate in self.gates:
                output = add_gate_to_pyquil_program(output, gate)
        return output

    def to_cirq(self, cirq_qubits=None):
        """Converts the circuit to a cirq Circuit object.
        NOTE: Here we always assume that the resulting circuit acts on a linear chain of
        qubits.

        Args:
            cirq_qubits: list[cirq.LineQubit]
                (optional) A list of cirq.LineQubit objects.
        """

        qubits = []
        if cirq_qubits == None:
            if self.qubits != None:
                if self.info["label"] == "cirq":
                    for q in self.qubits:
                        qkey = q.info["QubitKey"]
                        if q.info["QubitType"] == "GridQubit":
                            qubits.append(cirq.GridQubit(qkey[0], qkey[1]))
                        if q.info["QubitType"] == "LineQubit":
                            qubits.append(cirq.LineQubit(qkey))
                else:
                    qubits = [cirq.LineQubit(i) for i in self.get_qubits()]
        else:
            if len(cirq_qubits) < len(self.qubits):
                raise Exception(
                    "Input qubit register size is {}, which is not enough to represent this Circuit object that acts on {} qubits".format(
                        len(cirq_qubits), len(self.qubits)
                    )
                )
            qubits = cirq_qubits

        if self.gates != None:
            gates = [g.to_cirq(cirq_qubits) for g in self.gates]
        else:
            gates = []

        cirq_circuit = cirq.Circuit()
        cirq_circuit.append(gates, strategy=cirq.circuits.InsertStrategy.EARLIEST)
        return cirq_circuit

    def to_qiskit(self):
        """Converts the circuit to a qiskit QuantumCircuit object."""
        qiskit_circuit = qiskit.QuantumCircuit()  # New qiskit circuit object
        qreg = None
        creg = None

        if (
            self.qubits != None and self.qubits != []
        ):  # If there are qubits in the circuit, add them to the new qiskit circuit
            max_qindex = max([q.index for q in self.qubits])
            qreg = qiskit.QuantumRegister(max_qindex + 1, "q")
            creg = qiskit.ClassicalRegister(max_qindex + 1, "c")
            qiskit_circuit.add_register(qreg)
            qiskit_circuit.add_register(creg)

        if self.gates != None:
            for gate in self.gates:
                qiskit_gate_data = gate.to_qiskit(
                    qreg, creg
                )  # provide the gate conversion with the associated QuantumRegister
                N = len(
                    qiskit_gate_data
                )  # total number of entries in the list (which is 3x the number of elementary gates)
                if N % 3 != 0:
                    raise ValueError(
                        "The number of entries in qiskit_gate_data is {} which is not a multiple of 3".format(
                            N
                        )
                    )
                for index in np.linspace(0, N - 3, N // 3):
                    qiskit_circuit.append(
                        qiskit_gate_data[int(index)],
                        qargs=qiskit_gate_data[int(index) + 1],
                        cargs=qiskit_gate_data[int(index) + 2],
                    )

        return qiskit_circuit

    def to_dict(self, serialize_gate_params=True):
        """Creates a dictionary representing a circuit.

        Args:
            serialize_gate_params(bool): if true, it will change gate params from sympy to strings (if applicable)

        Returns:
            dictionary (dict): the dictionary
        """

        if self.gates != None:
            gates_entry = [
                gate.to_dict(serialize_params=serialize_gate_params)
                for gate in self.gates
            ]
        else:
            gates_entry = None

        if self.qubits != None:
            qubits_entry = [qubit.to_dict() for qubit in self.qubits]
        else:
            qubits_entry = None

        dictionary = {
            "schema": SCHEMA_VERSION + "-circuit",
            "name": self.name,
            "gates": gates_entry,
            "qubits": qubits_entry,
            "info": self.info,
        }

        return dictionary

    def to_unitary(self):
        """Creates a unitary matrix representing the circuit.

        Returns:
            An array representing the unitary matrix.
        """

        return self.to_cirq()._unitary_()

    def to_text_diagram(self, transpose=False):
        """Gets a text diagram representing the circuit.

        transpose (bool): if true, arrange qubit wires vertically instead of horizontally

        Returns:
            str: a string containing the text diagram
        """

        return self.to_cirq().to_text_diagram(transpose=transpose)

    def to_quil(self):
        """Gets the quil program representing the circuit.
        Returns:
            str: a string containing the quil program
        """
        return self.to_pyquil().out()

    def to_qpic(self):
        """Generates a string that can be used by qpic to build a picture of the circuit.

        Returns:
            str: a qpic string
        """

        qpic_string = ""

        for qubit in sorted(self.qubits, key=lambda q: q.index):
            qpic_string += "w{} W {}\n".format(qubit.index, qubit.index)

        for gate in self.gates:
            qpic_string += gate.to_qpic() + "\n"

        return qpic_string

    def __str__(self):
        """Get a string representation of the circuit.
        Returns:
            str: a string representation of the circuit
        """
        return self.to_text_diagram()

    @classmethod
    def from_dict(cls, dictionary):
        """Loads information of the circuit from a dictionary. This corresponds to the
        serialization routines to_dict for Circuit, Gate and Qubit.

        Args:
            dictionary (dict): the dictionary

        Returns:
            A core.circuit.Circuit object
        """

        output = cls(name=dictionary["name"])
        if dictionary["gates"] != None:
            output.gates = [Gate.from_dict(gate) for gate in dictionary["gates"]]
        else:
            output.gates = None

        if dictionary["qubits"] != None:
            output.qubits = [Qubit.from_dict(qubit) for qubit in dictionary["qubits"]]
        else:
            output.qubits = None
        output.info = dictionary["info"]
        return output

    def from_pyquil(self, pyquil_circuit):
        """Converts a pyquil Program object to a core.Circuit object.

        Args:
            pyquil_circuit: Program object(pyquil)
            name: string
                Name of the converted core.Circuit object.

        """

        self.info["label"] = "pyquil"

        _gatelist = []
        _qubits = []

        if len(pyquil_circuit) == 0:
            return

        _pyquil_qubits = []  # list of currently found *pyquil* qubits
        for gate in pyquil_circuit:

            _gatequbits = []
            for qubit in gate.qubits:

                def qubit_in_list(
                    qubit, qubitlist
                ):  # check if a pyquil qubit is in a list of pyquil qubits
                    output = False
                    out_index = []
                    for q in qubitlist:
                        if qubit.index == q.index:
                            output = True
                            out_index = q.index
                            break
                    return output, out_index

                _flag, _index = qubit_in_list(qubit, _pyquil_qubits)
                if _flag == False:
                    _pyquil_qubits.append(qubit)
                    _new_Qubit = Qubit.from_pyquil(qubit)
                    _qubits.append(_new_Qubit)
                    _gatequbits.append(_new_Qubit)
                else:
                    for q in _qubits:
                        if q.index == _index:
                            _old_Qubit = q
                            break
                    _gatequbits.append(_old_Qubit)

            _gatelist.append(Gate.from_pyquil(gate, _gatequbits))

        self.gates = _gatelist
        self.qubits = _qubits

    def from_cirq(self, cirq_circuit):
        """Convert from a cirq Circuit object to a core.Circuit object.

        Args:
            cirq_circuit: cirq Cirquit object.
                See the following: https://github.com/quantumlib/Cirq

        """
        self.info["label"] = "cirq"

        _gatelist = []
        _qubits = []

        if (
            len(cirq_circuit) == 0
            or sum([len(m.operations) for m in cirq_circuit]) == 0
        ):
            return

        _cirq_qubits = []  # list of currently found *cirq* qubits
        for moment in cirq_circuit:
            for op in moment.operations:
                _gatequbits = []
                for qubit in op.qubits:

                    def qubit_in_list(
                        qubit, qubitlist
                    ):  # check if a cirq qubit is in a list of cirq qubits
                        # if yes return the index
                        output = False
                        out_index = []
                        for q in qubitlist:
                            if isinstance(qubit, cirq.GridQubit) and isinstance(
                                q, cirq.GridQubit
                            ):
                                if qubit.row == q.row and qubit.col == q.col:
                                    output = True
                                    out_index = (q.row, q.col)
                                    break
                            elif isinstance(qubit, cirq.LineQubit) and isinstance(
                                qubit, cirq.LineQubit
                            ):
                                if qubit.x == q.x:
                                    output = True
                                    out_index = q.x
                                    break
                            else:
                                raise TypeError(
                                    "(Cirq) Qubit and Qubit list elements not of the same kind."
                                )
                        return output, out_index

                    _flag, _index = qubit_in_list(qubit, _cirq_qubits)
                    if _flag == False:  # if the qubit is not seen before
                        _cirq_qubits.append(
                            qubit
                        )  # add the cirq qubit to the list of cirq qubits seen
                        _new_Qubit = Qubit.from_cirq(
                            qubit, qubit.x
                        )  # generate a new qubit
                        _qubits.append(_new_Qubit)
                        _gatequbits.append(_new_Qubit)
                    else:  # if the qubit is already seen before
                        for (
                            q
                        ) in (
                            _qubits
                        ):  # search for the old Qubit object in the _qubits list
                            if q.info["QubitKey"] == _index:
                                _old_Qubit = q
                                break
                        _gatequbits.append(_old_Qubit)
                _gatelist.append(Gate.from_cirq(op, _gatequbits))

        self.gates = _gatelist
        self.qubits = _qubits

    def from_qiskit(self, qiskit_circuit):
        """Convert from a qiskit QuantumCircuit object to a core.circuit.Circuit object.

        Args:
            qiskit_circuit: qiskit QuantumCircuit object.

        """

        self.name = qiskit_circuit.name
        self.info["label"] = "qiskit"

        _gatelist = []  # list of gates for the output Circuit object
        _qubits = []  # list of qubits for the output Circuit object

        if len(qiskit_circuit.data) == 0:
            return

        _qiskit_qubits = []  # list of qiskit qubits in the circuit object
        for gate_data in qiskit_circuit.data:
            _gatequbits = []
            for qubit in gate_data[1]:

                def qubit_in_list(
                    qubit, qubitlist
                ):  # check if a qiskit qubit is in a list of qiskit qubit
                    output = False
                    for q in qubitlist:
                        if qubit == q:
                            output = True
                            break
                    return output

                if (
                    qubit_in_list(qubit, _qiskit_qubits) == 0
                ):  # if the qubit is not seen before
                    _qiskit_qubits.append(
                        qubit
                    )  # add the qiskit qubit to the list of currently seen qiskit qubits
                    _new_Qubit = Qubit.from_qiskit(
                        qubit, qubit.index
                    )  # generate a new Qubit object
                    _qubits.append(
                        _new_Qubit
                    )  # add to the list of Qubit objects for the output Circuit object
                    _gatequbits.append(
                        _new_Qubit
                    )  # add to the list of Qubit objects that the gate acts on
                else:  # if the qubit is already seen before
                    for (
                        q
                    ) in _qubits:  # search for the old Qubit object in the _qubits list
                        if q.info["num"] == qubit.index:
                            _old_Qubit = q
                            break
                    _gatequbits.append(_old_Qubit)

            zap_gate = Gate.from_qiskit(gate_data[0], _gatequbits)
            if zap_gate is not None:
                _gatelist.append(zap_gate)

        self.gates = _gatelist
        self.qubits = _qubits


def save_circuit(circuit, filename):
    """Saves a circuit object to a file.

    Args:
        circuit (core.Circuit): the circuit to be saved
        filename (str): the name of the file
    """

    with open(filename, "w") as f:
        f.write(json.dumps(circuit.to_dict(serialize_gate_params=True)))


def load_circuit(file):
    """Loads a circuit from a file.

    Args:
        file (str or file-like object): the name of the file, or a file-like object.

    Returns:
        circuit (core.Circuit): the circuit
    """

    if isinstance(file, str):
        with open(file, "r") as f:
            data = json.load(f)
    else:
        data = json.load(file)

    return Circuit.from_dict(data)


def save_circuit_set(circuit_set, filename):
    """Save a circuit set to a file.

    Args:
        circuit_set (list): a list of core.Circuit objects
        file (str or file-like object): the name of the file, or a file-like object
    """
    dictionary = {}
    dictionary["schema"] = SCHEMA_VERSION + "-circuit_set"
    dictionary["circuits"] = []
    for circuit in circuit_set:
        dictionary["circuits"].append(circuit.to_dict(serialize_gate_params=True))
    with open(filename, "w") as f:
        f.write(json.dumps(dictionary, indent=2))


def load_circuit_set(file):
    """Load a set of circuits from a file.

    Args:
        file (str or file-like object): the name of the file, or a file-like object.

    Returns:
        circuit_set (list): a list of core.Circuit objects
    """
    if isinstance(file, str):
        with open(file, "r") as f:
            data = json.load(f)
    else:
        data = json.load(file)

    circuit_set = []
    for circuit_dict in data["circuits"]:
        circuit_set.append(Circuit.from_dict(circuit_dict))
    return circuit_set


def pyquil2cirq(qprog):
    """Convert a pyquil Program to a cirq Circuit.

    Currently supports only common single- and two-qubit gates.

    Args:
        qprog (pyquil.quil.Program): the program to be converted.

    Returns:
        circuit (cirq.Cirquit): the converted circuit"""

    # A map between gate names used by pyquil and cirq gate objects
    op_map = {
        "X": cirq.X,
        "Y": cirq.Y,
        "Z": cirq.Z,
        "T": cirq.T,
        "H": cirq.H,
        "S": cirq.S,
        "RX": cirq.XPowGate,
        "RY": cirq.YPowGate,
        "RZ": cirq.ZPowGate,
        "CNOT": cirq.CNOT,
        "SWAP": cirq.SWAP,
        "CZ": cirq.CZ,
        "CPHASE": cirq.ops.common_gates.CZPowGate,
    }

    # Create the qubits. The row of each grid qubit is equal to the index
    # of the corresponding pyquil qubit.
    qubits = [cirq.GridQubit(i, 0) for i in qprog.get_qubits()]

    # A map between the row of the qubit and the index in the qubits array
    qubit_map = {}
    for i in range(len(qubits)):
        qubit_map[qubits[i].row] = i

    circuit = cirq.Circuit()

    for gate in qprog:
        if not op_map.get(gate.name):
            raise ValueError("Gate {} not yet supported".format(gate.name))

        # Find the cirq qubits that this gate acts on
        target_qubits = [qubits[qubit_map[q.index]] for q in gate.qubits]

        # Create the cirq gate
        if len(gate.params) == 0:
            cirq_gate = op_map[gate.name](*target_qubits)
        elif len(gate.params) == 1:
            cirq_gate = op_map[gate.name](exponent=gate.params[0] / np.pi)(
                *target_qubits
            )
        else:
            raise ValueError(
                "Gates with more than one parameter not yet supported: {}".format(gate)
            )

        # Append the gate to the circuit
        circuit.append(cirq_gate, strategy=cirq.circuits.InsertStrategy.EARLIEST)

    return circuit


def cirq2pyquil(circuit):
    """Convert a cirq Circuit to a pyquil Program.

    Currently supports only common single- and two-qubit gates.

    Args:
        circuit (cirq.Cirquit): the converted circuit.

    Returns:
        qprog (pyquil.quil.Program): the program to be converted."""

    # A map between cirq gate string representations and pyquil gate classes
    op_repr_map = {
        "cirq.X": pyquil.gates.X,
        "cirq.Y": pyquil.gates.Y,
        "cirq.Z": pyquil.gates.Z,
        "cirq.T": pyquil.gates.T,
        "cirq.H": pyquil.gates.H,
        "cirq.S": pyquil.gates.S,
        "cirq.CNOT": pyquil.gates.CNOT,
        "cirq.SWAP": pyquil.gates.SWAP,
        "cirq.CZ": pyquil.gates.CZ,
    }

    # A map between cirq gate classes and pyquil gate classes. Perhaps better to parse repr?
    op_type_map = {
        cirq.ops.common_gates.XPowGate: pyquil.gates.RX,
        cirq.ops.common_gates.YPowGate: pyquil.gates.RY,
        cirq.ops.common_gates.ZPowGate: pyquil.gates.RZ,
        cirq.ops.common_gates.CZPowGate: pyquil.gates.CPHASE,
    }

    # Create a map from row/column tuples to linear qubit index
    qubit_map = {}
    qubit_count = 0
    qubit = next(iter(circuit.all_qubits()))  # Grab a random qubit
    if isinstance(qubit, cirq.GridQubit):
        qubit_key = lambda q: (q.row, q.col)
    elif isinstance(qubit, cirq.LineQubit):
        qubit_key = lambda q: q.x
    else:
        raise ValueError("Qubit type {} not yet supported".format(type(qubit)))
    for qubit in sorted(circuit.all_qubits(), key=qubit_key):
        qubit_map[qubit_key(qubit)] = qubit_count
        qubit_count += 1

    # Create the program
    qprog = pyquil.quil.Program()

    def add_to_program(op):
        """Add a cirq op to the pyquil program qprog."""

        # Find the linear indices of the qubits acted on by this operation
        qubits = [qubit_map[qubit_key(q)] for q in op.qubits]

        # First check if the string representation matches known gates
        if op_repr_map.get(repr(op.gate)):
            qprog.inst(op_repr_map[repr(op.gate)](*qubits))

        # Next check if the type of the gate object matches known gates
        elif op_type_map.get(type(op.gate)):
            rads = op.gate.exponent * np.pi
            pyquil_gate = op_type_map[type(op.gate)]
            qprog.inst(pyquil_gate(rads, *qubits))

        # Decompose if PhasedXPowGate or HPowGate
        elif isinstance(op.gate, cirq.PhasedXPowGate) or isinstance(
            op.gate, cirq.HPowGate
        ):
            ops = cirq.decompose(op)
            for op in ops:
                add_to_program(op)

        elif isinstance(op.gate, cirq.XXPowGate):
            q1, q2 = op.qubits
            ops = [
                cirq.H(q1),
                cirq.H(q2),
                cirq.CNOT(q1, q2),
                cirq.rz(op.gate.exponent * pi)(q2),
                cirq.CNOT(q1, q2),
                cirq.H(q1),
                cirq.H(q2),
            ]
            for op in ops:
                add_to_program(op)

        elif isinstance(op.gate, cirq.YYPowGate):
            q1, q2 = op.qubits
            ops = [
                cirq.Z(q1) ** 0.5,
                cirq.Z(q2) ** 0.5,
                cirq.H(q1),
                cirq.H(q2),
                cirq.CNOT(q1, q2),
                cirq.rz(op.gate.exponent * pi)(q2),
                cirq.CNOT(q1, q2),
                cirq.H(q1),
                cirq.H(q2),
                cirq.Z(q1) ** -0.5,
                cirq.Z(q2) ** -0.5,
            ]
            for op in ops:
                add_to_program(op)

        elif isinstance(op.gate, cirq.ZZPowGate):
            q1, q2 = op.qubits
            ops = [
                cirq.CNOT(q1, q2),
                cirq.rz(op.gate.exponent * pi)(q2),
                cirq.CNOT(q1, q2),
            ]
            for op in ops:
                add_to_program(op)

        else:
            raise ValueError("Gate {} not yet supported".format(op.gate))

    for moment in circuit:
        for op in moment.operations:
            add_to_program(op)

    return qprog


def add_gate_to_pyquil_program(pyquil_program, gate):
    """Add the definition of a gate to a pyquil Program object if the gate is
    not currently defined.

    Args:
        pyquil_program: pyquil.Program
            The input Program object to which the gate is going to be added.
        gate: Gate (core.circuit)
            The Gate object describing the gate to be added.

    Returns:
        A new pyquil.Program object with the definition of the new gate being added.
    """

    if gate.name in COMMON_GATES:  # if a gate is already included in pyquil
        return pyquil_program + gate.to_pyquil()  # do nothing
    elif gate.name in UNIQUE_GATES:  # if a gate is unique to a specific package
        if gate.name == "ZXZ":
            beta = pyquil.quilatom.Parameter("beta")
            gamma = pyquil.quilatom.Parameter("gamma")
            zxz_unitary = np.array(
                [
                    [
                        quil_cos(gamma / 2),
                        -quil_sin(beta) * quil_sin(gamma / 2)
                        - 1j * quil_cos(beta) * quil_sin(gamma / 2),
                    ],
                    [
                        quil_sin(beta) * quil_sin(gamma / 2)
                        - 1j * quil_cos(beta) * quil_sin(gamma / 2),
                        quil_cos(gamma / 2),
                    ],
                ]
            )
            zxz_def = pyquil.quilbase.DefGate("ZXZ", zxz_unitary, [beta, gamma])
            ZXZ = zxz_def.get_constructor()
            return (
                pyquil_program
                + zxz_def
                + ZXZ(gate.params[0], gate.params[1])(gate.qubits[0].index)
            )
        if gate.name == "RH":
            beta = pyquil.quilatom.Parameter("beta")
            phase_factor = quil_cos(beta / 2) + 1j * quil_sin(beta / 2)
            elem00 = quil_cos(beta / 2) - 1j * 1 / np.sqrt(2) * quil_sin(beta / 2)
            elem01 = -1j * 1 / np.sqrt(2) * quil_sin(beta / 2)
            elem10 = -1j * 1 / np.sqrt(2) * quil_sin(beta / 2)
            elem11 = quil_cos(beta / 2) + 1j * 1 / np.sqrt(2) * quil_sin(beta / 2)
            rh_unitary = np.array(
                [
                    [phase_factor * elem00, phase_factor * elem01],
                    [phase_factor * elem10, phase_factor * elem11],
                ]
            )
            rh_def = pyquil.quilbase.DefGate("RH", rh_unitary, [beta])
            RH = rh_def.get_constructor()
            return pyquil_program + rh_def + RH(gate.params[0])(gate.qubits[0].index)
        if gate.name == "XX":  # XX gate (modified from XXPowGate in cirq)
            beta = pyquil.quilatom.Parameter("beta")
            elem_cos = quil_cos(beta)
            elem_sin = 1j * quil_sin(beta)
            xx_unitary = np.array(
                [
                    [elem_cos, 0, 0, elem_sin],
                    [0, elem_cos, elem_sin, 0],
                    [0, elem_sin, elem_cos, 0],
                    [elem_sin, 0, 0, elem_cos],
                ]
            )
            xx_def = pyquil.quilbase.DefGate("XX", xx_unitary, [beta])
            XX = xx_def.get_constructor()
            return (
                pyquil_program
                + xx_def
                + XX(gate.params[0])(gate.qubits[0].index, gate.qubits[1].index)
            )
        if gate.name == "YY":  # YY gate (modified from XXPowGate in cirq)
            beta = pyquil.quilatom.Parameter("beta")
            elem_cos = quil_cos(beta)
            elem_sin = 1j * quil_sin(beta)
            yy_unitary = np.array(
                [
                    [elem_cos, 0, 0, elem_sin],
                    [0, elem_cos, -elem_sin, 0],
                    [0, -elem_sin, elem_cos, 0],
                    [elem_sin, 0, 0, elem_cos],
                ]
            )
            yy_def = pyquil.quilbase.DefGate("YY", yy_unitary, [beta])
            YY = yy_def.get_constructor()
            return (
                pyquil_program
                + yy_def
                + YY(gate.params[0])(gate.qubits[0].index, gate.qubits[1].index)
            )
        if gate.name == "ZZ":  # ZZ gate (modified from XXPowGate in cirq)
            beta = pyquil.quilatom.Parameter("beta")
            elem_cos = quil_cos(beta)
            elem_sin = 1j * quil_sin(beta)
            zz_unitary = np.array(
                [
                    [elem_cos + elem_sin, 0, 0, 0],
                    [0, elem_cos - elem_sin, 0, 0],
                    [0, 0, elem_cos - elem_sin, 0],
                    [0, 0, 0, elem_cos + elem_sin],
                ]
            )
            zz_def = pyquil.quilbase.DefGate("ZZ", zz_unitary, [beta])
            ZZ = zz_def.get_constructor()
            return (
                pyquil_program
                + zz_def
                + ZZ(gate.params[0])(gate.qubits[0].index, gate.qubits[1].index)
            )
        if gate.name == "U1ex":  # IBM U1ex gate (arXiv:1805.04340v1)
            alpha = pyquil.quilatom.Parameter("alpha")
            beta = pyquil.quilatom.Parameter("beta")
            elem_cos = quil_cos(beta)
            elem_sin = 1j * quil_sin(beta)
            unitary = [[1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]]
            unitary[1][1] = quil_cos(alpha)
            unitary[2][2] = -quil_cos(alpha)
            unitary[2][1] = (quil_cos(beta) - 1j * quil_sin(beta)) * quil_sin(alpha)
            unitary[1][2] = (quil_cos(beta) + 1j * quil_sin(beta)) * quil_sin(alpha)
            u1ex_def = pyquil.quilbase.DefGate("U1ex", np.array(unitary), [alpha, beta])
            U1ex = u1ex_def.get_constructor()
            output_program = pyquil_program + U1ex(gate.params[0], gate.params[1])(
                gate.qubits[0].index, gate.qubits[1].index
            )
            gate_already_defined = False
            for gate_definition in pyquil_program.defined_gates:
                if gate_definition.name == "U1ex":
                    gate_already_defined = True
                    break
            if not gate_already_defined:
                output_program = output_program + u1ex_def
            return output_program
        if gate.name == "U2ex":  # IBM U2ex gate (arXiv:1805.04340v1)
            alpha = pyquil.quilatom.Parameter("alpha")
            unitary = [[1, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 0], [0, 0, 0, 1]]
            unitary[1][1] = quil_cos(2 * alpha)
            unitary[2][2] = quil_cos(2 * alpha)
            unitary[2][1] = -1j * quil_sin(2 * alpha)
            unitary[1][2] = -1j * quil_sin(2 * alpha)
            u2ex_def = pyquil.quilbase.DefGate("U2ex", np.array(unitary), [alpha])
            U2ex = u2ex_def.get_constructor()
            output_program = pyquil_program + U2ex(gate.params[0])(
                gate.qubits[0].index, gate.qubits[1].index
            )
            gate_already_defined = False
            for gate_definition in pyquil_program.defined_gates:
                if gate_definition.name == "U2ex":
                    gate_already_defined = True
                    break
            if not gate_already_defined:
                output_program = output_program + u1ex_def
            return output_program
        if gate.name == "MEASURE":
            reg_name = "r" + str(gate.qubits[0].index)

            ro = pyquil_program.declare(reg_name, "BIT", 1)
            return pyquil_program + MEASURE(gate.qubits[0].index, ro[0])
        if gate.name == "BARRIER":
            return pyquil_program


def add_ancilla_register_to_circuit(circuit, n_qubits_ancilla_register):
    """Add a register of ancilla qubits (qubit + identity gate) to an existing circuit.

    Args:
        circuit (core.Circuit): circuit to be extended
        n_qubits_ancilla_register (int): number of ancilla qubits to add
    Returns:
        core.Circuit: extended circuit

    """
    extended_circuit = Circuit()
    n_qubits = len(circuit.qubits)
    pyquil_circuit = circuit.to_pyquil()
    for i in range(n_qubits_ancilla_register):
        pyquil_circuit += I(n_qubits + i)
    extended_circuit.from_pyquil(pyquil_circuit)
    return extended_circuit
