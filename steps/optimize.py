import json
import numpy as np

from openfermion import SymbolicOperator
from typing import Union, Dict, Optional, List

from zquantum.core.circuit import load_circuit, load_circuit_template_params, Circuit
from zquantum.core.cost_function import get_ground_state_cost_function
from zquantum.core.estimator import BasicEstimator
from zquantum.core.serialization import save_optimization_results
from zquantum.core.utils import create_object


def optimize_parametrized_circuit_for_ground_state_of_operator(
    optimizer_specs: Union[Dict, str],
    target_operator: Union[SymbolicOperator, str],
    parametrized_circuit: Union[Circuit, str],
    backend_specs: Union[Dict, str],
    estimator_specs: Union[Dict, str] = None,
    epsilon: Optional[float] = None,
    delta: Optional[float] = None,
    initial_parameters: Union[str, np.ndarray, List[float]] = None,
    fixed_parameters: Optional[Union[np.ndarray, str]] = None,
    parameter_precision: Optional[float] = None,
    parameter_precision_seed: Optional[int] = None,
):
    """Optimize the parameters of a parametrized quantum circuit to prepare the ground state of a target operator.

    Args:
        optimizer_specs (Union[Dict, str]): The specs of the optimizer to use to refine the parameter values
        target_operator (Union[SymbolicOperator, str]): The operator of which to prepare the ground state
        parametrized_circuit (Union[Circuit, str]): The parametrized quantum circuit that prepares trial states
        backend_specs (Union[Dict, str]): The specs of the quantum backend (or simulator) to use to run the circuits
        estimator_specs (Union[Dict, str]): The estimator to use to estimate the expectation value of the operator.
            The default is the BasicEstimator.
        epsilon (Optional[float]): an additive/multiplicative error term. The cost function should be computed to within this error term.
        delta (Optional[float]): a confidence term. If theoretical upper bounds are known for the estimation technique,
            the final estimate should be within the epsilon term, with probability 1 - delta.
        initial_parameters (Union[str, np.ndarray, List[float]]): The initial parameter values to begin optimization
        fixed_parameters (Optional[Union[np.ndarray, str]]): values for the circuit parameters that should be fixed.
        parameter_precision (float): the standard deviation of the Gaussian noise to add to each parameter, if any.
        parameter_precision_seed (int): seed for randomly generating parameter deviation if using parameter_precision

        epsilon (Optional[float]):
        delta (Optional[float] = None,
        initial_parameters (Union[str, np.ndarray, List[float]] = None,
    """
    # for input_argument in [estimator_specs, epsilon, delta, initial_parameters]:
    if isinstance(optimizer_specs, str):
        optimizer_specs = json.loads(optimizer_specs)
    optimizer = create_object(optimizer_specs)

    if isinstance(target_operator, str):
        with open(target_operator, "r") as f:
            target_operator = json.loads(f.read())

    if isinstance(parametrized_circuit, str):
        parametrized_circuit = load_circuit(parametrized_circuit)

    if isinstance(backend_specs, str):
        backend_specs = json.loads(backend_specs)
    backend = create_object(backend_specs)

    if estimator_specs is not None:
        if isinstance(estimator_specs, str):
            estimator_specs = json.loads(estimator_specs)
        estimator = create_object(estimator_specs)
    else:
        estimator = BasicEstimator()

    if initial_parameters is not None:
        if isinstance(initial_parameters, str):
            initial_parameters = load_circuit_template_params(initial_parameters)

    if fixed_parameters is not None:
        if isinstance(fixed_parameters, str):
            fixed_parameters = load_circuit_template_params(fixed_parameters)

    cost_function = get_ground_state_cost_function(
        target_operator,
        parametrized_circuit,
        backend,
        estimator=estimator,
        epsilon=epsilon,
        delta=delta,
        fixed_parameters=fixed_parameters,
        parameter_precision=parameter_precision,
        parameter_precision_seed=parameter_precision_seed,
    )

    optimization_results = optimizer.minimize(cost_function, initial_parameters)

    save_optimization_results(optimization_results, "optimization_results.json")
