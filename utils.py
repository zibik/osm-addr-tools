from concurrent.futures import ThreadPoolExecutor
import functools
import multiprocessing

__executor = ThreadPoolExecutor(max_workers=multiprocessing.cpu_count()) # pick sane default - number of CPUs

def parallel_execution(*args):
    """run in parallel all functions passed as args"""
    return tuple(
        map(lambda x: x.result(),
            [__executor.submit(x) for x in args]
        )
    )

def parallel_map(func, lst):
    return tuple(
        map(lambda x: x.result(),
            [__executor.submit(functools.partial(func, x)) for x in lst]
        )
    )

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
    parallel_execution(lambda: sleep(5), lambda: sleep(5), lambda: sleep(5), lambda: sleep(5), lambda: sleep(5))
    print(time())


if __name__ == '__main__':
    main()
