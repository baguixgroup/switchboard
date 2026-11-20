"""Package a verified animation sheet into transparent PNG frames for native Laith.

This slices generated frames; it does not synthesize motion from a still image.
"""
from pathlib import Path
import argparse
import io
import json
import zipfile
from PIL import Image


def build(source,output,columns=4,rows=4):
    image=Image.open(source).convert('RGBA')
    if image.getchannel('A').getextrema()[0]!=0:raise ValueError('The animation sheet needs real transparent background pixels.')
    width=image.width//columns;height=image.height//rows
    left=(image.width-width*columns)//2;top=(image.height-height*rows)//2
    frames=[];output.mkdir(parents=True,exist_ok=True)
    for row in range(rows):
        for column in range(columns):
            x=left+column*width;y=top+row*height
            frame=image.crop((x,y,x+width,y+height));frame.thumbnail((256,256),Image.Resampling.LANCZOS);frames.append(frame)
    with zipfile.ZipFile(output/'laith-wave-frames.zip','w',compression=zipfile.ZIP_DEFLATED) as archive:
        for index,frame in enumerate(frames):
            stream=io.BytesIO();frame.save(stream,format='PNG');archive.writestr(f'frame-{index:03d}.png',stream.getvalue())
    (output/'laith-wave.json').write_text(json.dumps({'frames':len(frames),'fps':7,'idle_pause_ms':3500,'motion':'gentle paw wave'})+'\n')
    return frames


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('source',type=Path);parser.add_argument('--output',type=Path,default=Path('switchboard/web/static'))
    args=parser.parse_args();frames=build(args.source,args.output);print(f'Packaged {len(frames)} distinct animation frames with alpha.')
    preview=Path('dist/laith-wave-preview.webp');preview.parent.mkdir(exist_ok=True)
    frames[0].save(preview,save_all=True,append_images=frames[1:],duration=140,loop=0,lossless=True)
