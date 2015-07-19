""" Add utility indexes """
import py.path

projects_path = py.path.local().join('data', 'projects')
util_index_path = projects_path.join('_i_utility', 'False')

util_index_path.ensure(dir=True)

for data_path in projects_path.listdir('*.yaml'):
    index_ln_path = util_index_path.join(data_path.basename)
    index_ln_path.mksymlinkto(data_path)
