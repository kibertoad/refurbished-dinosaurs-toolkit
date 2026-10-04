import struct, subprocess, tempfile
from pathlib import Path
for sixteen,stereo in [(False,False),(False,True),(True,False),(True,True)]:
    bits=[]
    def put(v,n):bits.extend((v>>i)&1 for i in range(n))
    put(1,1);put(stereo,1);put(sixteen,1)
    channels=2 if stereo else 1; width=2 if sixteen else 1
    for i in range(channels*width):
        put(0,1);put(0,1);put(0 if sixteen and i%2 else 1,8);put(0,1)
    if stereo:put(0x6400 if sixteen else 0,width*8)
    put(0xFFFF if sixteen else 255,width*8)
    packet=bytearray(4+(len(bits)+7)//8)
    struct.pack_into('<I',packet,0,2*channels*width)
    for i,b in enumerate(bits):packet[4+i//8]|=b<<(i%8)
    frame=struct.pack('<I',len(packet)+4)+packet+b'\0'*4
    frame+=b'\0'*((-len(frame))%4)
    header=bytearray(104)
    header[:4]=b'SMK2'
    struct.pack_into('<IIII',header,4,4,4,1,100)
    struct.pack_into('<I',header,24,64)
    struct.pack_into('<I',header,72,22050|((0x80|(0x20 if sixteen else 0)|(0x10 if stereo else 0))<<24))
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'synthetic.smk';path.write_bytes(header+struct.pack('<I',len(frame))+b'\2'+frame)
        r=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-map','0:a:0','-f','s16le','-'],capture_output=True)
        expected=([-1,100,0,101] if stereo else [-1,0]) if sixteen else ([32512,-32768,-32768,-32512] if stereo else [32512,-32768])
        decoded=list(struct.unpack('<'+'h'*(len(r.stdout)//2),r.stdout))
        print(sixteen,stereo,'actual',decoded,'expected',expected,'error',r.stderr.decode().strip())
        assert r.returncode==0 and decoded==expected
