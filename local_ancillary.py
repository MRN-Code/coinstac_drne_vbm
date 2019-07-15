#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Apr 11 22:28:11 2018

@author: Harshvardhan
"""
import os
import warnings

import numpy as np
import pandas as pd
import scipy as sp
from numba import jit, prange

from ancillary import encode_png, print_beta_images, print_pvals
from parsers import parse_for_covar_info, perform_encoding

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import statsmodels.api as sm

MASK = os.path.join('/computation', 'mask_2mm.nii')


def mean_and_len_y(y):
    """Caculate the mean and length of each y vector"""
    meanY_vector = y.mean(axis=0)
    #    lenY_vector = y.count(axis=0)
    lenY_vector = np.count_nonzero(~np.isnan(y), axis=0)

    return meanY_vector, lenY_vector


@jit(nopython=True)
def gather_local_stats(X, y):
    """Calculate local statistics"""
    size_y = y.shape[1]

    params = np.zeros((X.shape[1], size_y))
    sse = np.zeros(size_y)
    tvalues = np.zeros((X.shape[1], size_y))
    rsquared = np.zeros(size_y)

    for voxel in prange(size_y):
        curr_y = y[:, voxel]
        beta_vector = np.linalg.inv(X.T @ X) @ (X.T @ curr_y)
        params[:, voxel] = beta_vector

        curr_y_estimate = np.dot(beta_vector, X.T)

        SSE_global = np.linalg.norm(curr_y - curr_y_estimate)**2
        SST_global = np.sum(np.square(curr_y - np.mean(curr_y)))

        sse[voxel] = SSE_global
        r_squared_global = 1 - (SSE_global / SST_global)
        rsquared[voxel] = r_squared_global

        dof_global = len(curr_y) - len(beta_vector)

        MSE = SSE_global / dof_global
        var_covar_beta_global = MSE * np.linalg.inv(X.T @ X)
        se_beta_global = np.sqrt(np.diag(var_covar_beta_global))
        ts_global = beta_vector / se_beta_global

        tvalues[:, voxel] = ts_global

    return (params, sse, tvalues, rsquared, dof_global)


def local_stats_to_dict_numba(args, X, y):
    """Wrap local statistics into a dictionary to be sent to the remote"""
    X1 = sm.add_constant(X)

    X_labels = list(X1.columns)

    X1 = X1.values.astype('float64')
    y1 = y.astype('float64')

    params, _, tvalues, _, dof_global = gather_local_stats(X1, y1)

    pvalues = 2 * sp.stats.t.sf(np.abs(tvalues), dof_global)

    #    keys = ["beta", "sse", "pval", "tval", "rsquared"]
    #
    #    values1 = pd.DataFrame(
    #        list(
    #            zip(params.T.tolist(), sse.tolist(), pvalues.T.tolist(),
    #                tvalues.T.tolist(), rsquared.tolist())),
    #        columns=keys)
    #
    #    local_stats_list = values1.to_dict(orient='records')

    beta_vector = params.T.tolist()

    print_pvals(args, pvalues.T, tvalues.T, X_labels)
    print_beta_images(args, beta_vector, X_labels)

    local_stats_list = encode_png(args)

    return beta_vector, local_stats_list


def local_stats_to_dict(X, y):
    """Calculate local statistics"""
    y_labels = list(y.columns)

    biased_X = sm.add_constant(X)

    local_params = []
    local_sse = []
    local_pvalues = []
    local_tvalues = []
    local_rsquared = []

    for column in y.columns:
        curr_y = list(y[column])

        # Printing local stats as well
        model = sm.OLS(curr_y, biased_X.astype(float)).fit()
        local_params.append(model.params)
        local_sse.append(model.ssr)
        local_pvalues.append(model.pvalues)
        local_tvalues.append(model.tvalues)
        local_rsquared.append(model.rsquared_adj)

    keys = ["beta", "sse", "pval", "tval", "rsquared"]
    local_stats_list = []

    for index, _ in enumerate(y_labels):
        values = [
            local_params[index].tolist(), local_sse[index],
            local_pvalues[index].tolist(), local_tvalues[index].tolist(),
            local_rsquared[index]
        ]
        local_stats_dict = {key: value for key, value in zip(keys, values)}
        local_stats_list.append(local_stats_dict)

        beta_vector = [l.tolist() for l in local_params]

    return beta_vector, local_stats_list


def add_site_covariates0(args, X):
    """Add site specific columns to the covariate matrix"""
    biased_X = sm.add_constant(X)
    site_covar_list = args["input"]["site_covar_list"]

    site_matrix = np.zeros((np.array(X).shape[0], len(site_covar_list)),
                           dtype=int)
    site_df = pd.DataFrame(site_matrix, columns=site_covar_list)

    select_cols = [
        col for col in site_df.columns if args["state"]["clientId"] in col
    ]

    site_df[select_cols] = 1

    biased_X.reset_index(drop=True, inplace=True)
    site_df.reset_index(drop=True, inplace=True)

    augmented_X = pd.concat([biased_X, site_df], axis=1)

    return augmented_X


def add_site_covariates00(args, original_args, X):
    """Perform dummy encoding on sub-sites
    """
    site_covar_json = args["input"]["site_covar_dict"]
    site_covar_dict = pd.read_json(site_covar_json)

    X = parse_for_covar_info(original_args)
    X = X.apply(pd.to_numeric, errors='ignore')
    biased_X = sm.add_constant(X)

    biased_X = biased_X.merge(site_covar_dict, left_on='site', right_on='site')
    biased_X.drop(columns='site', inplace=True)

    biased_X = pd.get_dummies(biased_X, drop_first=True)
    biased_X = biased_X * 1

    return biased_X


# TODO: Right now this only works for 'site' covariate. Need to extend to other
#    categorial covariates as well
def add_site_covariates(args, original_args, X):
    """Add site covariates based on information gathered from all sites
    """
    all_sites = args["input"]["site_list"]

    # Read original covariate_info
    X, _ = parse_for_covar_info(original_args)

    site_covar_dict = pd.get_dummies(all_sites, prefix='site')
    site_covar_dict.rename(index=dict(enumerate(all_sites)), inplace=True)
    site_covar_dict.index.name = 'site'
    site_covar_dict.reset_index(level=0, inplace=True)

    biased_X = X.merge(site_covar_dict, left_on='site', right_on='site')
    biased_X.drop(columns='site', inplace=True)

    biased_X = perform_encoding(biased_X)

    # TODO: Probably I can add it later in the end. Check.
    biased_X = sm.add_constant(X)

    return biased_X
