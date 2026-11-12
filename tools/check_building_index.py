"""Independently check depot v1 building index structure without loading it all."""
import argparse
import json
import math
import mmap
from pathlib import Path
import struct
import gzip
import shutil
import tempfile


def validate(path):
    if Path(path).suffix == '.gz':
        with tempfile.TemporaryDirectory(prefix='building-check-') as tmp:
            raw=Path(tmp)/'index.bin'
            with gzip.open(path,'rb') as src, open(raw,'wb') as dst:
                shutil.copyfileobj(src,dst,1024*1024)
            return _validate_raw(raw)
    return _validate_raw(path)


def _validate_raw(path):
    with open(path,'rb') as src:
        if Path(path).stat().st_size < 88:
            raise ValueError('Truncated building header')
        with mmap.mmap(src.fileno(),0,access=mmap.ACCESS_READ) as data:
            values=struct.unpack_from('<IBBHI IIIIIII d ddddd',data)
            magic,version,flags,reserved,count,cols,rows,rings,coords,cells,refs,reserved2,cs,depth,w,s,e,n=values
            if magic != 0x49424253 or version != 1 or flags or reserved or reserved2:
                raise ValueError('Unsupported building index header')
            if not count or not cols or not rows or not all(map(math.isfinite,(cs,depth,w,s,e,n))) or cs <= 0 or e < w or n < s:
                raise ValueError('Invalid building bounds or dimensions')
            offset=88+count*32+count*4
            offset=(offset+7)&~7
            building_rings=offset;offset+=(count+1)*4
            ring_coords=offset;offset+=(rings+1)*4
            offset=(offset+7)&~7
            coordinates=offset;offset+=coords*16
            row_starts=offset;offset+=(rows+1)*4
            cell_cols=offset;offset+=cells*4
            cell_offsets=offset;offset+=(cells+1)*4
            cell_ids=offset;offset+=refs*4
            if offset != len(data):
                raise ValueError('Building index length disagrees with header')

            def integers(start,size):
                for first in range(0,size,65536):
                    block=data[start+first*4:start+min(size,first+65536)*4]
                    yield from (value[0] for value in struct.iter_unpack('<I',block))

            for name,start,size,terminal in (
                ('building ring offsets',building_rings,count+1,rings),
                ('ring coordinate offsets',ring_coords,rings+1,coords),
                ('cell row offsets',row_starts,rows+1,cells),
                ('cell reference offsets',cell_offsets,cells+1,refs)):
                previous=0
                for i,value in enumerate(integers(start,size)):
                    if (i==0 and value!=0) or value < previous or value > terminal:
                        raise ValueError(f'Invalid {name}')
                    previous=value
                if previous != terminal:
                    raise ValueError(f'Invalid terminal {name}')
            if any(value>=cols for value in integers(cell_cols,cells)):
                raise ValueError('Cell column outside grid')
            if any(value>=count for value in integers(cell_ids,refs)):
                raise ValueError('Building reference outside record array')
            for first in range(0,coords,4096):
                block=data[coordinates+first*16:coordinates+min(coords,first+4096)*16]
                if any(not math.isfinite(v) for pair in struct.iter_unpack('<2d',block) for v in pair):
                    raise ValueError('Nonfinite building coordinate')
            return dict(buildings=count,rows=rows,columns=cols,rings=rings,coordinates=coords,
                        occupied_cells=cells,references=refs,bytes=len(data),bounds=[w,s,e,n])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path')
    print(json.dumps(validate(parser.parse_args().path),indent=2))
