import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
from hdbscan import HDBSCAN

def cluster_trips(df: gpd.GeoDataFrame,
                  time_scale: float,                 # spatiotemporal tradeoff (min / mi)
                  min_cluster_size: int = 6,         # typical microtransit van capacity
                  max_clus_size: int = 120,          # remove large superclusters
                  start_time: float = 6,             # 24-hr window start time
                  verbose: bool = False,             # verbose (optional)
                  **kwargs                           # HDBSCAN kwargs
                  ) -> gpd.GeoDataFrame:
    """
    Cluster taxi trips for microtransit vans using windowed HDBSCAN implementation
    """
    # generate time windows <= 24 hrs
    df = df.copy(); df['cluster_label'] = -1
    t0 = pd.Timedelta(hours = start_time)
    df['datetime_bin'] = (df.pickup_datetime - t0).dt.floor('1D')
    df['datetime_bin'] = (df.datetime_bin - df.datetime_bin.min()).dt.days
    df_win = df.groupby('datetime_bin', sort = True)

    # harmonize labels
    get_label = lambda v: v[v > -1].mode().iat[0] if (v > -1).any() else -1

    # loop over windows
    for i, df_i in df_win:
        if verbose:
            print(i, '—', df_i.pickup_datetime.min(), '-', df_i.pickup_datetime.max())

        # repeat rows for >1 passenger
        df_i = df_i.loc[df_i.index.repeat(df_i.passenger_count)]
        X_i = get_features(df_i, time_scale)

        # HDBSCAN clustering
        hdb = HDBSCAN(min_cluster_size = min_cluster_size, **kwargs)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', FutureWarning)
            labels = hdb.fit_predict(X_i)
        labels[labels > -1] += df.cluster_label.max() + 1
        
        # update labels
        labels = pd.Series(labels, index = df_i.index)
        labels = labels.groupby(level = 0).agg(get_label)
        df.loc[labels.index, 'cluster_label'] = labels

    # remove superclusters
    n_clus = df.groupby('cluster_label').passenger_count.sum()
    remove = n_clus.index[n_clus > max_clus_size]
    df.loc[df.cluster_label.isin(remove), 'cluster_label'] = -1

    # relabel clusters sequentially
    clus_msk = (df.cluster_label > -1)
    df.loc[clus_msk, 'cluster_label'] = pd.factorize(
        df.loc[clus_msk].cluster_label, sort = True)[0]
    df['cluster_label'] = df.cluster_label.astype(int)
    return df

def get_features(df: gpd.GeoDataFrame, time_scale: float) -> pd.DataFrame:
    """
    Create input features containing (x,y) values for pickup/dropoff + t (scaled; pickup only)
    """
    X = pd.DataFrame(index = df.index)
    X[['x0', 'x1']] = np.column_stack((df.pickup.x, df.dropoff.x))
    X[['y0', 'y1']] = np.column_stack((df.pickup.y, df.dropoff.y))
    X['t'] = df.pickup_datetime - df.pickup_datetime.min()
    X[['x0', 'y0', 'x1', 'y1']] /= 5280            # x,y (miles)
    X['t'] /= pd.Timedelta(minutes = time_scale)   # t (scaled min)
    return X

def get_statistics(df: gpd.GeoDataFrame) -> dict[str, pd.Series | float]:
    """
    Compute trip/passenger summary statistics for HDBSCAN clusters
    """
    df = df.loc[df.index.repeat(df.passenger_count)].copy()
    df_clus = df.loc[df.cluster_label > -1]
    labels = df_clus['cluster_label']
    df_group = df_clus.groupby('cluster_label', sort = True)

    # cluster size statistics
    frac_clus = df_clus.shape[0] / df.shape[0]
    clus_size = df_group.size()

    # RMS pickup distances (miles)
    x0 = df_clus.pickup.x.div(5280)
    y0 = df_clus.pickup.y.div(5280)
    x0c = x0 - x0.groupby(labels).transform('mean')
    y0c = y0 - y0.groupby(labels).transform('mean')
    rmsd_xy0 = ((x0c**2 + y0c**2).groupby(labels).mean())**.5

    # RMS dropoff distances (miles)
    x1 = df_clus.dropoff.x.div(5280)
    y1 = df_clus.dropoff.y.div(5280)
    x1c = x1 - x1.groupby(labels).transform('mean')
    y1c = y1 - y1.groupby(labels).transform('mean')
    rmsd_xy1 = ((x1c**2 + y1c**2).groupby(labels).mean())**.5

    # pickup & dropoff timing std (minutes)
    std_t0 = df_group.pickup_datetime.std().dt.total_seconds().div(60)
    std_t1 = df_group.dropoff_datetime.std().dt.total_seconds().div(60)
    
    # return dictionary
    return {'frac_clus' : frac_clus,
            'clus_size' : clus_size,
            'rmsd_xy0'  : rmsd_xy0,
            'rmsd_xy1'  : rmsd_xy1,
            'std_t0'    : std_t0,
            'std_t1'    : std_t1}

