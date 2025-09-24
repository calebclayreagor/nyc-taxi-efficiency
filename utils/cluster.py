import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
from hdbscan import HDBSCAN
from typing import Tuple

def cluster_trips(df: gpd.GeoDataFrame,
                  minutes_per_mile: int,             # spatiotemporal tradeoff
                  van_size: int = 6,                 # typical microtransit van
                  start_time: int = 6,               # windows start time
                  sample_frac: float | None = None,  # down-sample (optional)
                  verbose: bool = False,             # verbose (optional)
                  random_state: int = 1234
                  ) -> gpd.GeoDataFrame:
    """
    Cluster taxi trips for microtransit vans using windowed HDBSCAN implementation
    """
    # generate windows <= 24 hrs
    df = df.copy(); df['cluster_label'] = -1
    t0 = pd.Timedelta(hours = start_time)
    df['datetime_bin'] = (df.pickup_datetime - t0).dt.floor('1D')
    df['datetime_bin'] = (df.datetime_bin - df.datetime_bin.min()).dt.days
    if sample_frac:
        df = df.sample(frac = sample_frac, random_state = random_state)  # down-sample (optional)
    df_win = df.groupby('datetime_bin')

    # harmonize labels
    get_label = lambda v: v[v > -1].mode().iat[0] if (v > -1).any() else -1

    # loop over windows
    for i, df_i in df_win:
        if verbose:
            print(i, df_i.pickup_datetime.min(), df_i.pickup_datetime.max())

        # repeat rows for >1 passenger
        df_i = df_i.loc[df_i.index.repeat(df_i.passenger_count)]
        X_i = get_features(df_i, minutes_per_mile)

        # HDBSCAN clustering
        hdb = HDBSCAN(min_cluster_size = van_size)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', FutureWarning)
            labels = hdb.fit_predict(X_i)
        labels[labels > -1] += df.cluster_label.max() + 1
        
        # update labels
        labels = pd.Series(labels, index = df_i.index)
        labels = labels.groupby(level = 0).agg(get_label)
        df.loc[labels.index, 'cluster_label'] = labels
    return df

def get_features(df: gpd.GeoDataFrame, minutes_per_mile: int) -> pd.DataFrame:
    """
    Create input features containing (x,y) values for pickup/dropoff + scaled t (pickup only)
    """
    X = pd.DataFrame(index = df.index)
    X[['x0', 'x1']] = np.column_stack((df.pickup.x, df.dropoff.x))
    X[['y0', 'y1']] = np.column_stack((df.pickup.y, df.dropoff.y))
    X['t'] = df.pickup_datetime - df.pickup_datetime.min()
    X[['x0', 'y0', 'x1', 'y1']] /= 5280                     # x,y (miles)
    X['t'] /= pd.Timedelta(minutes = minutes_per_mile)      # t (scaled min)
    return X

def get_statistics(df: gpd.GeoDataFrame
                   ) -> Tuple[
                            float,   # passengers clustered (fraction)
                            float,   # passengers per cluster (mean)
                            int,     # largest cluster (passengers)
                            float,   # RMS of pickup locations [miles] (mean)
                            float,   # RMS of dropoff locations [miles] (mean)
                            float,   # Std of pickup times [minutes] (mean)
                            float]:  # Std of dropoff times [minutes] (mean)
    """
    Compute riders' summary statistics for HDBSCAN clusters
    """
    df = df.copy()
    df_clus = df.loc[df.cluster_label > -1]
    df_group = df_clus.groupby('cluster_label')

    # RMS for pickup/dropoff locations (x,y)
    get_rms = lambda col: np.sqrt(((col.x - col.x.mean()) ** 2 + \
                                   (col.y - col.y.mean()) **2).mean())

    # compute riders' statistics
    total_clus = df_clus.passenger_count.sum()                # total clustered
    frac_clus = total_clus / df.passenger_count.sum()         # frac clustered
    clus_size = df_group.passenger_count.sum()                # cluster sizes
    clus_size_mean = clus_size.mean()                         # cluster size (mean)
    clus_size_max = clus_size.max()                           # cluster size (max)
    rms_pickup_loc = df_group.pickup.apply(get_rms).mean()    # RMS pickup location [ft] (mean)
    rms_dropoff_loc = df_group.dropoff.apply(get_rms).mean()  # RMS dropoff location [ft] (mean)
    std_pickup_t = (df_group.pickup_datetime.std()
                    .dt.total_seconds().mean())               # Std pickup time [sec] (mean)
    std_dropoff_t = (df_group.dropoff_datetime.std()
                     .dt.total_seconds().mean())              # Std dropoff time [sec] (mean) 
    
    # return statistics
    return frac_clus, clus_size_mean, clus_size_max, \
            rms_pickup_loc / 5280, rms_dropoff_loc / 5280, \
            std_pickup_t / 60, std_dropoff_t / 60

