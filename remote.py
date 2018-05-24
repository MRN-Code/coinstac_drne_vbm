#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
This script includes the remote computations for single-shot ridge
regression with decentralized statistic calculation
"""
import base64
import numpy as np
import os
import regression as reg
import sys
import scipy as sp
import ujson as json
from remote_ancillary import get_stats_to_dict, print_pvals, print_beta_images


def remote_0(args):
    input_list = args["input"]
    site_ids = list(input_list.keys())
    site_covar_list = [
        '{}_{}'.format('site', label)
        for index, label in enumerate(site_ids) if index
    ]

    computation_output_dict = {
        "output": {
            "site_covar_list": site_covar_list,
            "computation_phase": "remote_0"
        },
        "cache": {}
    }

    return json.dumps(computation_output_dict)


def remote_1(args):
    input_list = args["input"]
    userID = list(input_list)[0]

    X_labels = input_list[userID]["X_labels"]
    y_labels = input_list[userID]["y_labels"]

    all_local_stats_dicts = [
        input_list[site]["local_stats_list"] for site in input_list
    ]

    beta_vector_0 = [
        np.array(input_list[site]["XtransposeX_local"]) for site in input_list
    ]

    beta_vector_1 = sum(beta_vector_0)

    avg_beta_vector = np.matrix.transpose(
        sum([
            np.matmul(
                sp.linalg.inv(beta_vector_1), input_list[site][
                    "Xtransposey_local"]) for site in input_list
        ]))

    mean_y_local = [input_list[site]["mean_y_local"] for site in input_list]
    count_y_local = [
        np.array(input_list[site]["count_local"]) for site in input_list
    ]
    mean_y_global = np.array(mean_y_local) * np.array(count_y_local)
    mean_y_global = np.average(mean_y_global, axis=0)

    dof_global = sum(count_y_local) - avg_beta_vector.shape[1]

    computation_output_dict = {
        "output": {
            "avg_beta_vector": avg_beta_vector.tolist(),
            "mean_y_global": mean_y_global.tolist(),
            "computation_phase": "remote_1"
        },
        "cache": {
            "avg_beta_vector": avg_beta_vector.tolist(),
            "mean_y_global": mean_y_global.tolist(),
            "dof_global": dof_global.tolist(),
            "X_labels": X_labels,
            "y_labels": y_labels,
            "local_stats_dict": all_local_stats_dicts
        }
    }

    return json.dumps(computation_output_dict)


def remote_2(args):
    """
    Computes the global model fit statistics, r_2_global, ts_global, ps_global

    Args:
        args (dictionary): {"input": {
                                "SSE_local": ,
                                "SST_local": ,
                                "varX_matrix_local": ,
                                "computation_phase":
                                },
                            "cache":{},
                            }

    Returns:
        computation_output (json) : {"output": {
                                        "avg_beta_vector": ,
                                        "beta_vector_local": ,
                                        "r_2_global": ,
                                        "ts_global": ,
                                        "ps_global": ,
                                        "dof_global":
                                        },
                                    "success":
                                    }
    Comments:
        Generate the local fit statistics
            r^2 : goodness of fit/coefficient of determination
                    Given as 1 - (SSE/SST)
                    where   SSE = Sum Squared of Errors
                            SST = Total Sum of Squares
            t   : t-statistic is the coefficient divided by its standard error.
                    Given as beta/std.err(beta)
            p   : two-tailed p-value (The p-value is the probability of
                  seeing a result as extreme as the one you are
                  getting (a t value as large as yours)
                  in a collection of random data in which
                  the variable had no effect.)

    """
    input_list = args["input"]

    X_labels = args["cache"]["X_labels"]

    all_local_stats_dicts = args["cache"]["local_stats_dict"]

    cache_list = args["cache"]
    avg_beta_vector = cache_list["avg_beta_vector"]
    dof_global = cache_list["dof_global"]

    SSE_global = sum(
        [np.array(input_list[site]["SSE_local"]) for site in input_list])
    SST_global = sum(
        [np.array(input_list[site]["SST_local"]) for site in input_list])
    varX_matrix_global = sum([
        np.array(input_list[site]["varX_matrix_local"]) for site in input_list
    ])

    r_squared_global = 1 - (SSE_global / SST_global)
    MSE = SSE_global / np.array(dof_global)

    ts_global = []
    ps_global = []

    for i in range(len(MSE)):
        var_covar_beta_global = MSE[i] * sp.linalg.inv(varX_matrix_global)
        se_beta_global = np.sqrt(var_covar_beta_global.diagonal())
        ts = (avg_beta_vector[i] / se_beta_global).tolist()
        ps = reg.t_to_p(ts, dof_global[i])
        ts_global.append(ts)
        ps_global.append(ps)

    print_pvals(args, ps_global, ts_global, X_labels)
    print_beta_images(args, avg_beta_vector, X_labels)

    # Begin code to serialize png images
    png_files = sorted(os.listdir(args["state"]["outputDirectory"]))

    encoded_png_files = []
    for file in png_files:
        if file.endswith('.png'):
            mrn_image = os.path.join(args["state"]["outputDirectory"], file)
            with open(mrn_image, "rb") as imageFile:
                mrn_image_str = base64.b64encode(imageFile.read())
            encoded_png_files.append(mrn_image_str)
    # End code to serialize png images

    # Block of code to print local stats as well
    sites = [site for site in input_list]

    all_local_stats_dicts = dict(zip(sites, all_local_stats_dicts))

    # Block of code to print just global stats
    global_dict_list = dict(zip(png_files, encoded_png_files))

    # Print Everything
    keys2 = ["global_stats", "local_stats"]
    output_dict = dict(zip(keys2, [global_dict_list, all_local_stats_dicts]))

    computation_output = {"output": output_dict, "success": True}

    return json.dumps(computation_output)


if __name__ == '__main__':

    parsed_args = json.loads(sys.stdin.read())
    phase_key = list(reg.list_recursive(parsed_args, 'computation_phase'))

    if "local_0" in phase_key:
        computation_output = remote_0(parsed_args)
        sys.stdout.write(computation_output)
    elif "local_1" in phase_key:
        computation_output = remote_1(parsed_args)
        sys.stdout.write(computation_output)
    elif "local_2" in phase_key:
        computation_output = remote_2(parsed_args)
        sys.stdout.write(computation_output)
    else:
        raise ValueError("Error occurred at Remote")