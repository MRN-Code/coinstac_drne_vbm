#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Apr 11 22:28:11 2018

@author: Harshvardhan
"""
import base64
import nibabel as nib
from nilearn import plotting
import numpy as np
import os
import pandas as pd
import scipy as sp
import warnings
from numba import jit, prange

with warnings.catch_warnings():
    warnings.simplefilter("ignore")
    import statsmodels.api as sm


def mean_and_len_y(y):
    """Caculate the length mean of each y vector"""
    meanY_vector = y.mean(axis=0).tolist()
    lenY_vector = y.count(axis=0).tolist()

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


def print_beta_images(args, avg_beta_vector, X_labels):
    beta_df = pd.DataFrame(avg_beta_vector, columns=X_labels)

    images_folder = args["state"]["outputDirectory"]

    mask_file = os.path.join(args["state"]["baseDirectory"], 'mask_6mm.nii')
    mask = nib.load(mask_file)

    for column in beta_df.columns:
        new_data = np.zeros(mask.shape)
        new_data[mask.get_data() > 0] = beta_df[column]

        clipped_img = nib.Nifti1Image(new_data, mask.affine, mask.header)

        plotting.plot_stat_map(
            clipped_img,
            output_file=os.path.join(images_folder, 'beta_' + str(column)),
            display_mode='ortho',
            colorbar=True)


def print_pvals(args, ps_global, ts_global, X_labels):
    p_df = pd.DataFrame(ps_global, columns=X_labels)
    t_df = pd.DataFrame(ts_global, columns=X_labels)

    # TODO manual entry, remove later
    images_folder = args["state"]["outputDirectory"]

    mask_file = os.path.join(args["state"]["baseDirectory"], 'mask_6mm.nii')
    mask = nib.load(mask_file)

    for column in p_df.columns:
        new_data = np.zeros(mask.shape)
        new_data[mask.get_data() >
                 0] = -1 * np.log10(p_df[column]) * np.sign(t_df[column])

        clipped_img = nib.Nifti1Image(new_data, mask.affine, mask.header)

        #        thresholdh = max(np.abs(p_df[column]))

        plotting.plot_stat_map(
            clipped_img,
            output_file=os.path.join(images_folder, 'pval_' + str(column)),
            display_mode='ortho',
            colorbar=True)


def local_stats_to_dict_numba(args, X, y):
    """Wrap local statistics into a dictionary to be sent to the remote"""
    X1 = sm.add_constant(X)
    X_labels = list(X1.columns)

    X1 = X1.values.astype('float64')
    y1 = y.values.astype('float64')

    params, sse, tvalues, rsquared, dof_global = gather_local_stats(X1, y1)

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

    # Begin code to serialize png images
    png_files = sorted(os.listdir(args["state"]["outputDirectory"]))

    encoded_png_files = []
    for file in png_files:
        if file.endswith('.png'):
            mrn_image = os.path.join(args["state"]["outputDirectory"], file)
            with open(mrn_image, "rb") as imageFile:
                mrn_image_str = base64.b64encode(imageFile.read())
            encoded_png_files.append(mrn_image_str)

    local_stats_list = dict(zip(png_files, encoded_png_files))

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


def add_site_covariates(args, X):
    """Add site specific columns to the covariate matrix"""
    biased_X = sm.add_constant(X)
    site_covar_list = args["input"]["site_covar_list"]

    site_matrix = np.zeros(
        (np.array(X).shape[0], len(site_covar_list)), dtype=int)
    site_df = pd.DataFrame(site_matrix, columns=site_covar_list)

    select_cols = [
        col for col in site_df.columns if args["state"]["clientId"] in col
    ]

    site_df[select_cols] = 1

    biased_X.reset_index(drop=True, inplace=True)
    site_df.reset_index(drop=True, inplace=True)

    augmented_X = pd.concat([biased_X, site_df], axis=1)

    return augmented_X