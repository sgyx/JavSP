import os
import sys
import random
import string
import pytest
from shutil import rmtree


sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from javsp.file import scan_movies


tmp_folder = 'TMP_' + ''.join(random.choices(string.ascii_uppercase, k=6))
DEFAULT_SIZE = 512*2**20    # 512 MiB

def touch_file_size(path: str, size_bytes: int):
    with open(path, 'wb') as f:
        f.seek(size_bytes - 1)
        f.write(b'\0')

@pytest.fixture
def prepare_files(files):
    """按照指定的文件列表创建对应的文件，并在测试完成后删除它们

    Args:
        files (list of tuple): 文件列表，仅接受相对路径
    """
    if not isinstance(files, dict):
        files = {i:DEFAULT_SIZE for i in files}
    for name, size in files.items():
        path = os.path.join(tmp_folder, name)
        folder = os.path.split(path)[0]
        if folder and (not os.path.exists(folder)):
            os.makedirs(folder)
        touch_file_size(path, size)
    yield
    rmtree(tmp_folder)
    return


# 根文件夹下的单个影片文件
@pytest.mark.parametrize('files', [('ABC-123.mp4',)])
def test_single_movie(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 1
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123.mp4'


# 多个分片以数字排序: 012
@pytest.mark.parametrize('files', [('ABC-123-0.mp4','ABC-123-1.mp4','ABC-123- 2.mp4')])
def test_scan_movies__012(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123-0.mp4'
    assert basenames[1] == 'ABC-123-1.mp4'
    assert basenames[2] == 'ABC-123- 2.mp4'


# 多个分片以数字排序: 123
@pytest.mark.parametrize('files', [('ABC-123.1.mp4','ABC-123. 2.mp4','ABC-123.3.mp4')])
def test_scan_movies__123(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123.1.mp4'
    assert basenames[1] == 'ABC-123. 2.mp4'
    assert basenames[2] == 'ABC-123.3.mp4'


# 多个分片以字母排序
@pytest.mark.parametrize('files', [('ABC-123-A.mp4','ABC-123-B.mp4','ABC-123- C .mp4')])
def test_scan_movies__abc(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123-A.mp4'
    assert basenames[1] == 'ABC-123-B.mp4'
    assert basenames[2] == 'ABC-123- C .mp4'


# 多个分片以.CDx编号
@pytest.mark.parametrize('files', [('ABC-123.CD1.mp4','ABC-123.CD2 .mp4','ABC-123.CD3.mp4')])
def test_scan_movies__cdx(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123.CD1.mp4'
    assert basenames[1] == 'ABC-123.CD2 .mp4'
    assert basenames[2] == 'ABC-123.CD3.mp4'


@pytest.mark.parametrize('files', [('abc123cd1.mp4','abc123cd2.mp4')])
def test_scan_movies__cdx_without_delimeter(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 2
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'abc123cd1.mp4'
    assert basenames[1] == 'abc123cd2.mp4'


# 文件夹以番号命名，分片位于文件夹内且无番号信息
@pytest.mark.parametrize('files', [('ABC-123/CD1.mp4','ABC-123/CD2 .mp4','ABC-123/CD3.mp4')])
def test_scan_movies__from_folder(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'CD1.mp4'
    assert basenames[1] == 'CD2 .mp4'
    assert basenames[2] == 'CD3.mp4'


# 分片以多位数字编号
@pytest.mark.parametrize('files', [('ABC-123.01.mp4','ABC-123.02.mp4','ABC-123.03.mp4')])
def test_scan_movies__0x123(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 3
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'ABC-123.01.mp4'
    assert basenames[1] == 'ABC-123.02.mp4'
    assert basenames[2] == 'ABC-123.03.mp4'


# 无效: 没有可以匹配到番号的文件
@pytest.mark.parametrize('files', [('什么也没有.mp4',)])
def test_scan_movies__nothing(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效: 在CWD下没有可以匹配到番号的文件
@pytest.mark.parametrize('files', [('什么也没有.mp4',)])
def test_scan_movies__nothing_in_cwd(prepare_files):
    cwd = os.getcwd()
    os.chdir(tmp_folder)
    try:
        movies = scan_movies('.')
    finally:
        os.chdir(cwd)
    assert len(movies) == 0


# 无效：多个分片命名杂乱
@pytest.mark.parametrize('files', [('ABC-123-1.mp4','ABC-123-第2部分.mp4','ABC-123-3.mp4')])
def test_scan_movies__strange_names(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效：同一影片的分片和非分片混合
@pytest.mark.parametrize('files', [('ABC-123.mp4','ABC-123-1.mp4','ABC-123-2.mp4')])
def test_scan_movies__mix_slices(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效：多个分片位于不同文件夹
@pytest.mark.parametrize('files', [('ABC-123.CD1.mp4','sub/ABC-123.CD2.mp4','ABC-123.CD3.mp4')])
def test_scan_movies__wrong_structure(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效：分片的起始编号不合法
@pytest.mark.parametrize('files', [('ABC-123.CD2.mp4','ABC-123.CD3.mp4','ABC-123.CD4.mp4')])
def test_scan_movies__wrong_initial_id(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效：分片的编号不连续
@pytest.mark.parametrize('files', [('ABC-123.CD1.mp4','ABC-123.CD3.mp4','ABC-123.CD4.mp4')])
def test_scan_movies__not_consecutive(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 无效：分片的编号重复
@pytest.mark.parametrize('files', [('ABC-123-1.mp4','ABC-123-1 .mp4','ABC-123-3.mp4')])
def test_scan_movies__duplicate_index(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 混合有效和无效数据
@pytest.mark.parametrize('files', [('DEF-456/movie.mp4', 'ABC-123.1.mp4','sub/ABC-123.2.mp4','ABC-123.3.mp4')])
def test_scan_movies__mix_data(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'DEF-456'
    assert len(movies[0].files) == 1
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames[0] == 'movie.mp4'


# 文件夹以番号命名，文件夹内同时有带番号的影片和广告
@pytest.mark.parametrize('files', [{'ABC-123/ABC-123.mp4': DEFAULT_SIZE, 'ABC-123/广告1.mp4': 1024, 'ABC-123/广告2.mp4': 243269631}])
def test_scan_movies__1_video_with_ad(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == 'ABC-123'
    assert len(movies[0].files) == 1


# 文件夹内同时有多部带番号的影片和广告
@pytest.mark.parametrize('files', [{'ABC-123.mp4': DEFAULT_SIZE, 'DEF-456.mp4': DEFAULT_SIZE, '广告1.mp4': 1024, '广告2.mp4': 243269631}])
def test_scan_movies__n_video_with_ad(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 2
    assert movies[0].dvdid == 'ABC-123' and movies[1].dvdid == 'DEF-456'
    assert all(len(i.files) == 1 for i in movies)


# 同一个番号属于不同的无码片商时，应视为不同的影片（如一本道和カリビアンコムプレミアム都有123119_001）
@pytest.mark.parametrize('files', [('1pondo_123119_001.mp4', 'caribpr_123119_001.mp4')])
def test_scan_movies__same_id_different_studios(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 2
    assert {m.dvdid for m in movies} == {'123119_001'}
    assert {m.studio_hint for m in movies} == {'1pondo', 'caribpr'}
    for m in movies:
        assert len(m.files) == 1
        assert os.path.basename(m.files[0]).startswith(m.studio_hint)


# 不同片商的同番号影片位于不同文件夹时，也应视为不同的影片
@pytest.mark.parametrize('files', [('A/1pondo_123119_001.mp4', 'B/caribpr_123119_001.mp4')])
def test_scan_movies__same_id_different_studios_in_folders(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 2
    assert {m.studio_hint for m in movies} == {'1pondo', 'caribpr'}


# 带有片商名称的多分片影片
@pytest.mark.parametrize('files', [('1pondo_012717_472-1.mp4', '1pondo_012717_472-2.mp4')])
def test_scan_movies__studio_slices(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 1
    assert movies[0].dvdid == '012717_472'
    assert movies[0].studio_hint == '1pondo'
    basenames = [os.path.basename(i) for i in movies[0].files]
    assert basenames == ['1pondo_012717_472-1.mp4', '1pondo_012717_472-2.mp4']


# 同一片商的同番号影片位于不同文件夹时，仍视为重复影片而略过
@pytest.mark.parametrize('files', [('A/1pondo_123119_001.mp4', 'B/1pondo_123119_001.mp4')])
def test_scan_movies__same_studio_duplicates(prepare_files):
    movies = scan_movies(tmp_folder)
    assert len(movies) == 0


# 部分文件名不带片商名称时，若该番号只对应一个片商，则归入该片商（避免同一部影片被当成两部分别整理）
@pytest.mark.parametrize('files', [('A/1pondo_012717_472.mp4', 'B/012717_472.mp4')])
def test_scan_movies__unlabeled_file_joins_only_studio(prepare_files):
    movies = scan_movies(tmp_folder)
    # 两个文件被归为同一部影片，但位于不同文件夹，因此按重复影片略过（与没有片商信息时的行为一致）
    assert len(movies) == 0
