from typing import Any, Dict

import torch
from torch.fx import GraphModule  # type: ignore
from torch.fx import map_arg  # type: ignore
from torch.fx.graph import Graph
from torch.quantization._numeric_suite import (
    get_logger_dict,
    get_matching_activations,
    prepare_model_with_stubs,
    compare_weights,
    OutputLogger,
    ShadowLogger,
)
from torch.quantization.fx.quantization_patterns import NumericSuiteQuantizeHandler
from torch.quantization.fx.quantize import _remove_qconfig, is_activation_post_process
from torch.quantization.quantize_fx import prepare_fx
from torch.quantization import get_default_compare_output_module_list

def remove_qconfig_observer_fx(model):
    # remove activation post process
    act_post_process_removed_graph = Graph()
    env = {}  # type: Dict[str, Any]

    modules = dict(model.named_modules())

    def load_arg(a):
        return map_arg(a, lambda node: env[node.name])

    for node in model.graph.nodes:
        if node.op == "output":
            act_post_process_removed_graph.output(map_arg(node.args[0], load_arg))
            continue
        if node.op == "call_module" and is_activation_post_process(
            modules[node.target]
        ):
            # remove activation post process node
            env[node.name] = env[node.args[0].name]
        else:
            env[node.name] = act_post_process_removed_graph.node_copy(node, load_arg)

    _remove_qconfig(model)
    model = GraphModule(model, act_post_process_removed_graph)
    return model


def compare_weights_fx(float_dict, quantized_dict):
    r"""Compare the weights of the float module with its corresponding quantized
    module. Return a dict with key corresponding to module names and each entry being
    a dictionary with two keys 'float' and 'quantized', containing the float and
    quantized weights. This dict can be used to compare and compute the quantization
    error of the weights of float and quantized models.

    Example usage:
        prepared_model = prepare_fx(float_model, qconfig_dict)
        backup_prepared_model = copy.deepcopy(prepared_model)
        quantized_model = convert_fx(prepared_model)

        qmodel = quantized_model
        wt_compare_dict = compare_weights_fx(backup_prepared_model.state_dict(), qmodel.state_dict())
        for key in wt_compare_dict:
            print(key, compute_error(wt_compare_dict[key]['float'], wt_compare_dict[key]['quantized'].dequantize()))

    Args:
        float_dict: state dict of the float model (prepared model)
        quantized_dict: state dict of the quantized model

    Return:
        weight_dict: dict with key corresponding to module names and each entry being
        a dictionary with two keys 'float' and 'quantized', containing the float and
        quantized weights
    """
    torch._C._log_api_usage_once(
        "quantization_api._numeric_suite_fx.compare_weights_fx"
    )
    return compare_weights(float_dict, quantized_dict)


def prepare_model_with_stubs_fx(float_module, q_module, module_swap_list, Logger):
    r"""Prepare the model by attaching the float module to its matching quantized
    module as the shadow if the float module type is in module_swap_list.

    Example usage:
        prepare_model_with_stubs_fx(float_model, q_model, module_swap_list, Logger)
        q_model(data)
        ob_dict = get_logger_dict(q_model)

    Args:
        float_module: float module used to generate the q_module
        q_module: module quantized from float_module
        module_swap_list: list of float module types to attach the shadow
        Logger: type of logger to be used in shadow module to process the outputs of
            quantized module and its float shadow module
    """
    torch._C._log_api_usage_once(
        "quantization_api._numeric_suite.prepare_model_with_stubs_fx"
    )
    return prepare_model_with_stubs(float_module, q_module, module_swap_list, Logger)


def compare_model_stub_fx(
    float_model, q_model, module_swap_list, *data, Logger=ShadowLogger
):
    r"""Compare quantized module in a model with its floating point counterpart,
    feeding both of them the same input. Return a dict with key corresponding to
    module names and each entry being a dictionary with two keys 'float' and
    'quantized', containing the output tensors of quantized and its matching
    float shadow module. This dict can be used to compare and compute the module
    level quantization error.

    This function first call prepare_model_with_stubs_fx() to swap the quantized
    module that we want to compare with the Shadow module, which takes quantized
    module, corresponding float module and logger as input, and creates a forward
    path inside to make the float module to shadow quantized module sharing the
    same input. The logger can be customizable, default logger is ShadowLogger
    and it will save the outputs of the quantized module and float module that
    can be used to compute the module level quantization error.

    Example usage:
        module_swap_list = [nn.Linear]
        ob_dict = compare_model_stub_fx(float_model,qmodel,module_swap_list, data)
        for key in ob_dict:
            print(key, compute_error(ob_dict[key]['float'], ob_dict[key]['quantized'].dequantize()))

    Args:
        float_model: float model used to generate the q_model
        q_model: model quantized from float_model
        module_swap_list: list of float module types at which shadow modules will
            be attached.
        data: input data used to run the prepared q_model
        Logger: type of logger to be used in shadow module to process the outputs of
            quantized module and its float shadow module
    """
    torch._C._log_api_usage_once(
        "quantization_api._numeric_suite.compare_model_stub_fx"
    )
    float_model = remove_qconfig_observer_fx(float_model)
    prepare_model_with_stubs_fx(float_model, q_model, module_swap_list, Logger)
    q_model(*data)
    ob_dict = get_logger_dict(q_model)
    return ob_dict


def prepare_model_outputs_fx(
    float_module, q_module, Logger=OutputLogger, allow_list=None
):
    r"""Prepare the model by attaching the logger to both float module
    and quantized module if they are in the allow_list.

    Args:
        float_module: float module used to generate the q_module
        q_module: module quantized from float_module
        Logger: type of logger to be attached to float_module and q_module
        allow_list: list of module types to attach logger
    """
    torch._C._log_api_usage_once(
        "quantization_api._numeric_suite.prepare_model_outputs_fx"
    )
    if allow_list is None:
        allow_list = get_default_compare_output_module_list()

    float_module = remove_qconfig_observer_fx(float_module)

    qconfig_debug = torch.quantization.QConfig(activation=Logger, weight=None)
    qconfig_dict = {"": qconfig_debug}

    additional_quant_patterns = {}

    for module in allow_list:
        additional_quant_patterns[module] = NumericSuiteQuantizeHandler

    prepare_custom_config_dict = {"additional_quant_pattern": additional_quant_patterns}

    float_module = prepare_fx(float_module, qconfig_dict, prepare_custom_config_dict)
    q_module = prepare_fx(q_module, qconfig_dict, prepare_custom_config_dict)

    return float_module, q_module


def compare_model_outputs_fx(
    float_model, q_model, *data, Logger=OutputLogger, allow_list=None
):
    r"""Compare output activations between float and quantized models at
    corresponding locations for the same input. Return a dict with key corresponding
    to quantized module names and each entry being a dictionary with two keys
    'float' and 'quantized', containing the activations of quantized model and
    float model at matching locations. This dict can be used to compare and
    compute the propagation quantization error.

    Example usage:
        act_compare_dict = compare_model_outputs_fx(float_model, qmodel, data)
        for key in act_compare_dict:
            print(key, compute_error(act_compare_dict[key]['float'], act_compare_dict[key]['quantized'].dequantize()))

    Args:
        float_model: float model used to generate the q_model
        q_model: model quantized from float_model
        data: input data used to run the prepared float_model and q_model
        Logger: type of logger to be attached to float_module and q_module
        allow_list: list of module types to attach logger

    Return:
        act_compare_dict: dict with key corresponding to quantized module names
        and each entry being a dictionary with two keys 'float' and 'quantized',
        containing the matching float and quantized activations
    """
    torch._C._log_api_usage_once(
        "quantization_api._numeric_suite.compare_model_outputs_fx"
    )
    if allow_list is None:
        allow_list = get_default_compare_output_module_list()

    float_model, q_model = prepare_model_outputs_fx(float_model, q_model, Logger, allow_list)
    float_model(*data)
    q_model(*data)
    act_compare_dict = get_matching_activations(float_model, q_model)
    return act_compare_dict
