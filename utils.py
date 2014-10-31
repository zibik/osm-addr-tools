from concurrent.futures import ThreadPoolExecutor
__executor = ThreadPoolExecutor(max_workers=4) # pick sane default

def parallel_execution(*args):
    """run in parallel all functions passed as args"""
    return tuple(
        map(lambda x: x.result(),
            [__executor.submit(x) for x in args]
        )
    )

def main():
    from time import sleep,time
    print(time())
    parallel_execution(lambda: sleep(5), lambda: sleep(5), lambda: sleep(5), lambda: sleep(5), lambda: sleep(5))
    print(time())


if __name__ == '__main__':
    main()
