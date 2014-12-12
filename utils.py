from concurrent.futures import ThreadPoolExecutor
import functools
import multiprocessing
import itertools

__executor = ThreadPoolExecutor(max_workers=multiprocessing.cpu_count()) # pick sane default - number of CPUs

def parallel_execution(*args):
    """run in parallel all functions passed as args"""
    return tuple(
        map(lambda x: x.result(),
            [__executor.submit(x) for x in args]
        )
    )

def parallel_map(func, lst):
    cpu = multiprocessing.cpu_count()
    iters = (itertools.islice(lst, x, None, cpu) for x in range(cpu))
    def loop(l):
        return tuple(map(func, l))
    
    return tuple(itertools.chain.from_iterable(
            zip(*parallel_execution(*tuple(functools.partial(loop, x) for x in iters)))
   ))


def groupby(lst, keyfunc=lambda x: x, valuefunc=lambda x: x):
    ret = {}
    for i in lst:
        key = keyfunc(i)
        try:
            entry = ret[key]
        except KeyError:
            entry = []
            ret[key] = entry
        entry.append(valuefunc(i))
    return ret

def main():
    from time import sleep,time
    print(time())
    parallel_execution(lambda: sleep(1), lambda: sleep(1), lambda: sleep(1), lambda: sleep(1), lambda: sleep(1))
    print(time())
    def f(x):
        sleep(1)
        return str(x)
    print(parallel_map(f, [1,2,3,4,5,6,7,8,9,10,11,12,13]))
    print(time())


if __name__ == '__main__':
    main()
