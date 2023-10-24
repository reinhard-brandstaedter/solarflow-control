from datetime import datetime, timedelta
from functools import reduce

class TimewindowBuffer:
    def __init__(self, minutes: int = 2):
        self.values = []
        self.minutes = minutes

    def __str__(self):
        return "[ " + ",".join([f'{v[1]:>3.1f}' for v in self.values]) + " ]"

    def add(self,value):
        now = datetime.now()
        self.values.append((now,value))
        first_ts = self.values[0][0]
        diff = now - first_ts
        if diff.total_seconds()/60 > self.minutes:
            self.values.pop(0)

    def avg(self) -> float:
        return reduce(lambda a,b: a+b, [v[1] for v in self.values])/len(self.values)