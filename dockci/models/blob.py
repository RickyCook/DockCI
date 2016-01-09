import hashlib

import py.path


CHUNK_SIZE = 4000


class FilesystemBlob(object):
    """ On-disk blob data storage used to access data by hash """

    def __init__(self, store_dir, etag, split_levels=3, split_size=2):
        self.store_dir = store_dir
        self.etag = etag
        self.split_levels = split_levels
        self.split_size = split_size

    @classmethod
    def from_files(cls, store_dir, file_paths, **kwargs):
        """
        Create a ``FilesystemBlob`` object from file paths, using their hash as
        an etag

        Examples:

        >>> first_path_1 = py.path.local('/tmp/dockci_doctest_a')
        >>> with first_path_1.open('w') as handle: \
                handle.write('content')
        7

        >>> second_path_1 = py.path.local('/tmp/dockci_doctest_b')
        >>> with second_path_1.open('w') as handle: \
                handle.write('more content')
        12

        >>> FilesystemBlob.from_files(None, [first_path_1, second_path_1]).etag
        'def71336ff0befa04a2c210810ddbf6cf137fc86'

        >>> first_path_2 = first_path_1.dirpath().join('dockci_doctest_c')
        >>> first_path_1.move(first_path_2)

        >>> FilesystemBlob.from_files(None, [first_path_2, second_path_1]).etag
        'def71336ff0befa04a2c210810ddbf6cf137fc86'

        >>> dir_path = py.path.local('/tmp/dockci_doctest_dir')
        >>> dir_path.ensure_dir()
        local('/tmp/dockci_doctest_dir')

        >>> first_path_3 = dir_path.join('dockci_doctest_a')
        >>> first_path_2.move(first_path_3)
        >>> second_path_3 = dir_path.join('dockci_doctest_b')
        >>> second_path_1.move(second_path_3)

        >>> FilesystemBlob.from_files(None, [first_path_3, second_path_3]).etag
        'def71336ff0befa04a2c210810ddbf6cf137fc86'

        >>> FilesystemBlob.from_files(None, [second_path_3, first_path_3]).etag
        'def71336ff0befa04a2c210810ddbf6cf137fc86'

        >>> with first_path_3.open('w') as handle: \
                handle.write('different content')
        17

        >>> FilesystemBlob.from_files(None, [second_path_3, first_path_3]).etag
        '1e756fb51dce67082ad0cac701ecfd11cdc9f845'

        >>> with second_path_3.open('w') as handle: \
                handle.write('more different content')
        22

        >>> FilesystemBlob.from_files(None, [second_path_3, first_path_3]).etag
        'f72eb637bf583da17013d6b95383a4fe54cafe9e'
        """
        digests = []
        for file_path in file_paths:
            with file_path.open('rb') as handle:
                file_hash = hashlib.sha1()

                chunk = None
                while chunk is None or len(chunk) == CHUNK_SIZE:
                    chunk = handle.read(CHUNK_SIZE)
                    file_hash.update(chunk)

                digests.append(file_hash.digest())

        all_hash = hashlib.sha1()
        for digest in sorted(digests):
            all_hash.update(digest)

        return cls(store_dir, all_hash.hexdigest(), **kwargs)

    @property
    def _etag_split_iter(self):
        """
        Range object for the split

        Examples:

        >>> list(FilesystemBlob('None', 'None', 3, 2)._etag_split_iter)
        [0, 2, 4]

        >>> list(FilesystemBlob('None', 'None', 4, 2)._etag_split_iter)
        [0, 2, 4, 6]

        >>> list(FilesystemBlob('None', 'None', 3, 3)._etag_split_iter)
        [0, 3, 6]
        """
        return range(0, self.split_levels * self.split_size, self.split_size)

    @property
    def path(self):
        """
        ``py.path.local`` path to the blob

        Examples:

        >>> blob = FilesystemBlob(py.path.local('/test'), 'abcdefghijkl')
        >>> blob.path.strpath
        '/test/ab/cd/ef/abcdefghijkl'

        >>> blob = FilesystemBlob(py.path.local('/test'), \
                                  'abcdefghijkl', \
                                  split_levels=4)
        >>> blob.path.strpath
        '/test/ab/cd/ef/gh/abcdefghijkl'

        >>> blob = FilesystemBlob(py.path.local('/test'), \
                                  'abcdefghijkl', \
                                  split_size=3)
        >>> blob.path.strpath
        '/test/abc/def/ghi/abcdefghijkl'

        >>> blob = FilesystemBlob(py.path.local('/other'), 'abcdefghijkl')
        >>> blob.path.strpath
        '/other/ab/cd/ef/abcdefghijkl'
        """
        return self.store_dir.join(*[
            self.etag[idx:idx + self.split_size]
            for idx in self._etag_split_iter
        ] + [self.etag])

