from datetime import datetime
from datetime import timedelta
from functools import reduce
from threading import Timer
import logging
import sys
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.linear_model import LinearRegression

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")

class RepeatedTimer:
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.interval   = interval
        self.function   = function
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False

def isExpired(value, now, maxage):
    diff = now - value[0]
    return diff.total_seconds() < maxage


class TimewindowBuffer:
    def __init__(self, minutes: int = 2):
        self.aggregated_values = []
        self.minutes = minutes
        self.values = []

    def __str__(self):
        return "[ " + ",".join([f'{v:>3.1f}' for v in self.aggregated_values]) + " ]"


    def add(self,value):
        now = datetime.now()
        self.values.append((now,value))

        self.values = list(filter(lambda v: isExpired(v, now, self.minutes*60),self.values))
        #self.aggregated_values = list(filter(lambda v: isExpired(v, now, self.minutes*60),self.aggregated_values))

        # create moving averages of 10s back from most recent values
        self.aggregated_values = []
        avg = last_avg = 0
        i = 1
        while True:
            bucket = list(filter(lambda v: isExpired(v, now-timedelta(seconds=i*10), 10),self.values))
            avg = reduce(lambda a,b: a+b, [v[1] for v in bucket])/len(bucket)
            self.aggregated_values.insert(0,avg)
            #log.info(f' Bucket {i}: {[v[1] for v in enumerate(bucket)]}')
            #log.info(self.aggregated_values)
            if avg == last_avg or i == 6:
                break
            else:
                last_avg = avg
                i += 1

    # number of entries in buffer
    def len(self):
        return len(self.aggregated_values)
    
    # most recent measurement
    def last(self) -> float:
        n = len(self.aggregated_values)
        if n == 0: return 0
        return round(self.aggregated_values[-1],1)
    
    def previous(self) -> float:
        n = len(self.aggregated_values)
        if n < 2: return 0
        return round(self.aggregated_values[-2],1)
    
    # standard moving average
    def avg(self) -> float:
        n = len(self.aggregated_values)
        if n == 0: return 0
        return round(reduce(lambda a,b: a+b, [v[1] for v in self.aggregated_values])/n,1)
    
    # weighted moving average
    def wavg(self) -> float:
        n = len(self.aggregated_values)
        if n == 0: return 0
        return round(reduce(lambda a,b: a+b, self.aggregated_values)/((n*(n+1))/2),1)

    # n^2 weighted moving average
    def qwavg(self) -> float:
        n = len(self.aggregated_values)
        if n == 0: return 0
        return round(reduce(lambda a,b: a+b, self.aggregated_values)/((n*(n+1)*(2*n+1))/6),1)
    
    def clear(self):
        #self.values = []
        self.aggregated_values = [self.aggregated_values[-1]]

    def predict(self) -> []:
        if len(self.aggregated_values) >= 5:
            data = {'X': [i for i,v in enumerate(self.values)],
                    'y': [v[1] for i,v in enumerate(self.values)]}
            df = pd.DataFrame(data)
            X = df["X"]
            X = np.array(X).reshape(-1,1)
            y = df["y"]

            model = LinearRegression()
            model.fit(X,y)
            
            y_pred = model.predict(np.array([[6]]))
            log.debug(f'prediction of {self}: {y_pred}')

            return list(map(lambda x: round(x,1), y_pred))
        else:
            return [self.aggregated_values[-1]] if len(self.aggregated_values) > 0 else [0]

    
def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys.split("."), dictionary)