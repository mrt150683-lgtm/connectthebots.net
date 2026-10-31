# Original music bed for the 46-second film: additive pads, plucks and bells, numpy only.
# Usage: python3 music.py OUT.wav
import sys
import numpy as np, soundfile as sf
SR=48000; DUR=46.0; N=int(SR*DUR); rs=np.random.RandomState(7)
L=np.zeros(N); Rt=np.zeros(N)
def hz(n): return 440*2**((n-69)/12)
NOTE={'C':0,'C#':1,'D':2,'D#':3,'E':4,'F':5,'F#':6,'G':7,'G#':8,'A':9,'A#':10,'B':11}
def m(s): return NOTE[s[:-1]]+12*(int(s[-1])+1)
def add(sig,t0,pan=0.0,g=1.0):
    i=int(t0*SR); j=min(N,i+len(sig)); 
    if i>=N: return
    s=sig[:j-i]*g; L[i:j]+=s*np.sqrt((1-pan)/2); Rt[i:j]+=s*np.sqrt((1+pan)/2)
def env(n,a,r,total):
    t=np.arange(n)/SR; e=np.minimum(1,t/a)*np.clip((total-t)/r,0,1); return e
def pad(note,t0,t1,g=.05,att=1.6,rel=2.2):
    d=t1-t0+rel; n=int(d*SR); t=np.arange(n)/SR; f=hz(note); s=np.zeros(n)
    for det,pan in ((-5,-.5),(0,0),(5,.5)):
        ff=f*2**(det/1200)
        for k in range(1,9):
            if ff*k>9000: break
            s+=np.sin(2*np.pi*ff*k*t+rs.rand()*6.28)/k**1.7
    lfo=1+.12*np.sin(2*np.pi*.17*t+rs.rand()*6)
    add(s*env(n,att,rel,d)*lfo*g/3,t0,pan=(rs.rand()-.5)*.6)
def pluck(note,t0,g=.12,pan=0):
    d=1.4; n=int(d*SR); t=np.arange(n)/SR; f=hz(note)
    s=np.sin(2*np.pi*f*t)*np.exp(-t*4.2)+.22*np.sin(2*np.pi*f*3.93*t)*np.exp(-t*14)+.1*np.sin(2*np.pi*f*2*t)*np.exp(-t*7)
    s*=np.minimum(1,t/.004); add(s*g,t0,pan)
def bell(note,t0,g=.12,pan=0):
    d=4.5; n=int(d*SR); t=np.arange(n)/SR; f=hz(note)
    s=np.sin(2*np.pi*f*t)*np.exp(-t*1.1)+.35*np.sin(2*np.pi*f*2.756*t)*np.exp(-t*2.4)+.18*np.sin(2*np.pi*f*5.404*t)*np.exp(-t*4)
    s*=np.minimum(1,t/.003); add(s*g,t0,pan)
def sub(note,t0,t1,g=.09):
    d=t1-t0+1.5; n=int(d*SR); t=np.arange(n)/SR; add(np.sin(2*np.pi*hz(note)*t)*env(n,1.2,1.5,d)*g,t0)
CH=[(0,9.3,'D2',['D3','A3','C#4','E4','F#4']),(9.3,12.6,'D2',['D3','A3','C#4','E4','F#4']),
    (12.6,15.6,'B1',['D3','F#3','A3','C#4']),(15.6,18.6,'G1',['B2','D3','F#3','A3']),
    (18.6,23.3,'E2',['G3','B3','D4','F#4']),(23.3,26.0,'G1',['B2','D3','F#3','A3']),
    (26.0,28.4,'A1',['D3','E3','F#3','B3']),(28.4,31.6,'B1',['D3','F#3','A3','C#4']),
    (31.6,35.0,'G1',['B2','D3','F#3','A3']),(35.0,38.5,'A1',['E3','A3','C#4','E4']),
    (38.5,42.5,'D2',['D3','A3','F#4','C#5','E5'])]
for t0,t1,b,ns in CH:
    gp=.05 if t0<38 else .065
    for n in ns: pad(m(n),t0,t1,g=gp*(.7 if t0<5 else 1))
    sub(m(b),t0,t1,g=.11 if t0>=12.6 else .07)
# plucks: probes from 12.6, sparse while letting go, full again, resolve
beat=60/84; e8=beat/2
pat=[0,2,1,3,2,1,3,2]
t=12.6; k=0
while t<38.4:
    ch=[c for c in CH if c[0]<=t<c[1]][0]; ns=[m(n)+12 for n in ch[3]]
    sparse=18.6<=t<23.3
    if not sparse or k%2==0:
        v=.075*(1.0 if k%4==0 else .7)*(.75 if sparse else 1)*min(1,(t-12.6)/2+.3)
        pluck(ns[pat[k%8]%len(ns)],t,g=v,pan=((k%3)-1)*.35)
    t+=e8; k+=1
bell(m('A5'),9.35,.11,.1); bell(m('D6'),9.38,.05,-.2)
bell(m('F#5'),28.5,.08,-.2); bell(m('A5'),28.9,.06,.2)
bell(m('C#6'),35.0,.07,.25); bell(m('E6'),36.2,.07,-.25)
bell(m('D5'),38.6,.12,0); bell(m('A5'),38.62,.08,.3); bell(m('F#6'),38.7,.05,-.3)
# air: soft filtered noise swell into the reveal and the ending
def swell(t0,t1,g):
    n=int((t1-t0)*SR); x=rs.randn(n); X=np.fft.rfft(x); f=np.fft.rfftfreq(n,1/SR); X*=np.exp(-((f-2500)/1800)**2); x=np.fft.irfft(X,n)
    e=np.sin(np.linspace(0,np.pi,n))**2; add(x/np.abs(x).max()*e*g,t0)
swell(4.8,9.6,.035); swell(36.3,39.2,.04)
# reverb
def verb(x,seed):
    r=np.random.RandomState(seed); n=int(2.8*SR); ir=r.randn(n)*np.exp(-np.arange(n)/SR*2.3); ir[:int(.02*SR)]*=np.linspace(0,1,int(.02*SR))
    m_=len(x)+n; F=1<<int(np.ceil(np.log2(m_))); y=np.fft.irfft(np.fft.rfft(x,F)*np.fft.rfft(ir,F),F)[:len(x)]; return y/np.sqrt((ir**2).sum())
wl,wr=verb(L,1),verb(Rt,2)
out=np.stack([L*.75+wl*.38,Rt*.75+wr*.38],1)
fade=np.ones(N); i=int(42.6*SR); fade[i:]=np.cos(np.linspace(0,np.pi/2,N-i))**2; out*=fade[:,None]
fi=int(.6*SR); out[:fi]*=np.linspace(0,1,fi)[:,None]
out/=np.abs(out).max()/0.8
sf.write(sys.argv[1] if len(sys.argv)>1 else 'music.wav',out,SR); print('ok',out.shape)
