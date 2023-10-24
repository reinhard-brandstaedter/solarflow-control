from datetime import datetime, timedelta
from functools import reduce
import logging
import sys

FORMAT = '%(asctime)s:%(levelname)s: %(message)s'
logging.basicConfig(stream=sys.stdout, level="INFO", format=FORMAT)
log = logging.getLogger("")
class TimewindowBuffer:
    def __init__(self, minutes: int = 2):
        self.values = []
        self.minutes = minutes

    def __str__(self):
        return "[ " + ",".join([f'{v[1]:>3.1f}' for v in self.values]) + " ]"

    def add(self,value):
        now = datetime.now()
        self.values.append((now,value))
        while True:
            first_ts = self.values[0][0]
            diff = now - first_ts
            if diff.total_seconds()/60 > self.minutes:
                self.values.pop(0)
            else:
                break

    # standard moving average
    def avg(self) -> float:
        n = len(self.values)
        if n == 0: return 0
        return reduce(lambda a,b: a+b, [v[1] for v in self.values])/n
    
    # weighted moving average
    def wavg(self) -> float:
        n = len(self.values)
        if n == 0: return 0
        return reduce(lambda a,b: a+b, [v[1]*(i+1) for i,v in enumerate(self.values)])/((n*(n+1))/2)
    
def deep_get(dictionary, keys, default=None):
    return reduce(lambda d, key: d.get(key, default) if isinstance(d, dict) else default, keys.split("."), dictionary)