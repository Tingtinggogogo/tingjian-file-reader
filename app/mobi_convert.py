"""Resource-bounded native decoder; no uploaded code or decryption keys."""
import os
import resource
import sys

if __name__ == '__main__':
    resource.setrlimit(resource.RLIMIT_CPU, (40, 40))
    resource.setrlimit(resource.RLIMIT_FSIZE, (80 * 1024 * 1024, 80 * 1024 * 1024))
    if sys.platform == 'linux':
        resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024))
    os.execvp('mobitool', ['mobitool', '-e', '-o', sys.argv[2], sys.argv[1]])
