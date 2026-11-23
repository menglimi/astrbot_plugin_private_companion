(()=>{var nl="183",ur={LEFT:0,MIDDLE:1,RIGHT:2,ROTATE:0,DOLLY:1,PAN:2},dr={ROTATE:0,PAN:1,DOLLY_PAN:2,DOLLY_ROTATE:3},dp=0,Lh=1,pp=2;var Dr=1,fp=2,Us=3,Ns=0,In=1,En=2,Ei=0,Ya=1,Za=2,Dh=3,Uh=4,mp=5;var Os=100,gp=101,_p=102,vp=103,yp=104,xp=200,Mp=201,bp=202,Sp=203,Tp=204,Ep=205,wp=206,Ap=207,Cp=208,Rp=209,Pp=210,Ip=211,Lp=212,Dp=213,Up=214,Nh=0,Oh=1,Fh=2,Kl=3,Bh=4,zh=5,kh=6,Hh=7,Np=0,Op=1,Fp=2,pi=0,Gh=1,Vh=2,Wh=3,Ja=4,jh=5,Xh=6,qh=7;var Yh=300,Fs=301,Ur=302,$l=303,Ql=304,Ka=306,ms=1e3,ar=1001,il=1002,ni=1003,Bp=1004;var $a=1005;var nn=1006,ec=1007;var Nr=1008;var Yn=1009,Zh=1010,Jh=1011,Bs=1012,tc=1013,qi=1014,si=1015,wi=1016,nc=1017,ic=1018,zs=1020,Kh=35902,$h=35899,zp=1021,kp=1022,fi=1023,pr=1026,Or=1027,rc=1028,sc=1029,ks=1030,Qh=1031;var eu=1033,ac=33776,oc=33777,lc=33778,cc=33779,tu=35840,nu=35841,iu=35842,ru=35843,su=36196,au=37492,ou=37496,lu=37488,cu=37489,hu=37490,uu=37491,du=37808,pu=37809,fu=37810,mu=37811,gu=37812,_u=37813,vu=37814,yu=37815,xu=37816,Mu=37817,bu=37818,Su=37819,Tu=37820,Eu=37821,wu=36492,Au=36494,Cu=36495,Ru=36283,Pu=36284,Iu=36285,Lu=36286;var _a=2300,rl=2301,el=2302,vh=2303,yh=2400,xh=2401,Mh=2402;var Hp=0,Gp=1,Fr="",Xt="srgb",Rr="srgb-linear",va="linear",bt="srgb";var Cr=7680;var Vp=512,Wp=513,jp=514,hc=515,Xp=516,qp=517,uc=518,Yp=519,bh=35044,dc=35048;var Du="300 es",Xi=2e3,gs=2001;function cm(r){return ArrayBuffer.isView(r)&&!(r instanceof DataView)}function _s(r){return document.createElementNS("http://www.w3.org/1999/xhtml",r)}function Zp(){let r=_s("canvas");return r.style.display="block",r}var Rd={},vs=null;function Uu(...r){let e="THREE."+r.shift();vs?vs("log",e,...r):console.log(e,...r)}function Jp(r){let e=r[0];if(typeof e=="string"&&e.startsWith("TSL:")){let t=r[1];t&&t.isStackTrace?r[0]+=" "+t.getLocation():r[1]='Stack trace not available. Enable "THREE.Node.captureStackTrace" to capture stack traces.'}return r}function tt(...r){let e="THREE."+(r=Jp(r)).shift();if(vs)vs("warn",e,...r);else{let t=r[0];t&&t.isStackTrace?console.warn(t.getError(e)):console.warn(e,...r)}}function rt(...r){let e="THREE."+(r=Jp(r)).shift();if(vs)vs("error",e,...r);else{let t=r[0];t&&t.isStackTrace?console.error(t.getError(e)):console.error(e,...r)}}function ya(...r){let e=r.join(" ");e in Rd||(Rd[e]=!0,tt(...r))}function Kp(r,e,t){return new Promise(function(n,i){setTimeout(function s(){switch(r.clientWaitSync(e,r.SYNC_FLUSH_COMMANDS_BIT,0)){case r.WAIT_FAILED:i();break;case r.TIMEOUT_EXPIRED:setTimeout(s,t);break;default:n()}},t)})}var $p={[Nh]:1,[Fh]:6,[Bh]:7,[Kl]:5,[Oh]:0,[kh]:2,[Hh]:4,[zh]:3},bi=class{addEventListener(e,t){this._listeners===void 0&&(this._listeners={});let n=this._listeners;n[e]===void 0&&(n[e]=[]),n[e].indexOf(t)===-1&&n[e].push(t)}hasEventListener(e,t){let n=this._listeners;return n!==void 0&&n[e]!==void 0&&n[e].indexOf(t)!==-1}removeEventListener(e,t){let n=this._listeners;if(n===void 0)return;let i=n[e];if(i!==void 0){let s=i.indexOf(t);s!==-1&&i.splice(s,1)}}dispatchEvent(e){let t=this._listeners;if(t===void 0)return;let n=t[e.type];if(n!==void 0){e.target=this;let i=n.slice(0);for(let s=0,a=i.length;s<a;s++)i[s].call(this,e);e.target=null}}},mn=["00","01","02","03","04","05","06","07","08","09","0a","0b","0c","0d","0e","0f","10","11","12","13","14","15","16","17","18","19","1a","1b","1c","1d","1e","1f","20","21","22","23","24","25","26","27","28","29","2a","2b","2c","2d","2e","2f","30","31","32","33","34","35","36","37","38","39","3a","3b","3c","3d","3e","3f","40","41","42","43","44","45","46","47","48","49","4a","4b","4c","4d","4e","4f","50","51","52","53","54","55","56","57","58","59","5a","5b","5c","5d","5e","5f","60","61","62","63","64","65","66","67","68","69","6a","6b","6c","6d","6e","6f","70","71","72","73","74","75","76","77","78","79","7a","7b","7c","7d","7e","7f","80","81","82","83","84","85","86","87","88","89","8a","8b","8c","8d","8e","8f","90","91","92","93","94","95","96","97","98","99","9a","9b","9c","9d","9e","9f","a0","a1","a2","a3","a4","a5","a6","a7","a8","a9","aa","ab","ac","ad","ae","af","b0","b1","b2","b3","b4","b5","b6","b7","b8","b9","ba","bb","bc","bd","be","bf","c0","c1","c2","c3","c4","c5","c6","c7","c8","c9","ca","cb","cc","cd","ce","cf","d0","d1","d2","d3","d4","d5","d6","d7","d8","d9","da","db","dc","dd","de","df","e0","e1","e2","e3","e4","e5","e6","e7","e8","e9","ea","eb","ec","ed","ee","ef","f0","f1","f2","f3","f4","f5","f6","f7","f8","f9","fa","fb","fc","fd","fe","ff"],Pd=1234567,ps=Math.PI/180,ys=180/Math.PI;function Br(){let r=4294967295*Math.random()|0,e=4294967295*Math.random()|0,t=4294967295*Math.random()|0,n=4294967295*Math.random()|0;return(mn[255&r]+mn[r>>8&255]+mn[r>>16&255]+mn[r>>24&255]+"-"+mn[255&e]+mn[e>>8&255]+"-"+mn[e>>16&15|64]+mn[e>>24&255]+"-"+mn[63&t|128]+mn[t>>8&255]+"-"+mn[t>>16&255]+mn[t>>24&255]+mn[255&n]+mn[n>>8&255]+mn[n>>16&255]+mn[n>>24&255]).toLowerCase()}function ut(r,e,t){return Math.max(e,Math.min(t,r))}function Sh(r,e){return(r%e+e)%e}function fa(r,e,t){return(1-t)*r+t*e}function ds(r,e){switch(e.constructor){case Float32Array:return r;case Uint32Array:return r/4294967295;case Uint16Array:return r/65535;case Uint8Array:return r/255;case Int32Array:return Math.max(r/2147483647,-1);case Int16Array:return Math.max(r/32767,-1);case Int8Array:return Math.max(r/127,-1);default:throw new Error("Invalid component type.")}}function Sn(r,e){switch(e.constructor){case Float32Array:return r;case Uint32Array:return Math.round(4294967295*r);case Uint16Array:return Math.round(65535*r);case Uint8Array:return Math.round(255*r);case Int32Array:return Math.round(2147483647*r);case Int16Array:return Math.round(32767*r);case Int8Array:return Math.round(127*r);default:throw new Error("Invalid component type.")}}var gt={DEG2RAD:ps,RAD2DEG:ys,generateUUID:Br,clamp:ut,euclideanModulo:Sh,mapLinear:function(r,e,t,n,i){return n+(r-e)*(i-n)/(t-e)},inverseLerp:function(r,e,t){return r!==e?(t-r)/(e-r):0},lerp:fa,damp:function(r,e,t,n){return fa(r,e,1-Math.exp(-t*n))},pingpong:function(r,e=1){return e-Math.abs(Sh(r,2*e)-e)},smoothstep:function(r,e,t){return r<=e?0:r>=t?1:(r=(r-e)/(t-e))*r*(3-2*r)},smootherstep:function(r,e,t){return r<=e?0:r>=t?1:(r=(r-e)/(t-e))*r*r*(r*(6*r-15)+10)},randInt:function(r,e){return r+Math.floor(Math.random()*(e-r+1))},randFloat:function(r,e){return r+Math.random()*(e-r)},randFloatSpread:function(r){return r*(.5-Math.random())},seededRandom:function(r){r!==void 0&&(Pd=r);let e=Pd+=1831565813;return e=Math.imul(e^e>>>15,1|e),e^=e+Math.imul(e^e>>>7,61|e),((e^e>>>14)>>>0)/4294967296},degToRad:function(r){return r*ps},radToDeg:function(r){return r*ys},isPowerOfTwo:function(r){return!(r&r-1)&&r!==0},ceilPowerOfTwo:function(r){return Math.pow(2,Math.ceil(Math.log(r)/Math.LN2))},floorPowerOfTwo:function(r){return Math.pow(2,Math.floor(Math.log(r)/Math.LN2))},setQuaternionFromProperEuler:function(r,e,t,n,i){let s=Math.cos,a=Math.sin,o=s(t/2),c=a(t/2),l=s((e+n)/2),h=a((e+n)/2),p=s((e-n)/2),f=a((e-n)/2),m=s((n-e)/2),v=a((n-e)/2);switch(i){case"XYX":r.set(o*h,c*p,c*f,o*l);break;case"YZY":r.set(c*f,o*h,c*p,o*l);break;case"ZXZ":r.set(c*p,c*f,o*h,o*l);break;case"XZX":r.set(o*h,c*v,c*m,o*l);break;case"YXY":r.set(c*m,o*h,c*v,o*l);break;case"ZYZ":r.set(c*v,c*m,o*h,o*l);break;default:tt("MathUtils: .setQuaternionFromProperEuler() encountered an unknown order: "+i)}},normalize:Sn,denormalize:ds},Te=class r{constructor(e=0,t=0){r.prototype.isVector2=!0,this.x=e,this.y=t}get width(){return this.x}set width(e){this.x=e}get height(){return this.y}set height(e){this.y=e}set(e,t){return this.x=e,this.y=t,this}setScalar(e){return this.x=e,this.y=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setComponent(e,t){switch(e){case 0:this.x=t;break;case 1:this.y=t;break;default:throw new Error("index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;default:throw new Error("index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y)}copy(e){return this.x=e.x,this.y=e.y,this}add(e){return this.x+=e.x,this.y+=e.y,this}addScalar(e){return this.x+=e,this.y+=e,this}addVectors(e,t){return this.x=e.x+t.x,this.y=e.y+t.y,this}addScaledVector(e,t){return this.x+=e.x*t,this.y+=e.y*t,this}sub(e){return this.x-=e.x,this.y-=e.y,this}subScalar(e){return this.x-=e,this.y-=e,this}subVectors(e,t){return this.x=e.x-t.x,this.y=e.y-t.y,this}multiply(e){return this.x*=e.x,this.y*=e.y,this}multiplyScalar(e){return this.x*=e,this.y*=e,this}divide(e){return this.x/=e.x,this.y/=e.y,this}divideScalar(e){return this.multiplyScalar(1/e)}applyMatrix3(e){let t=this.x,n=this.y,i=e.elements;return this.x=i[0]*t+i[3]*n+i[6],this.y=i[1]*t+i[4]*n+i[7],this}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this}clamp(e,t){return this.x=ut(this.x,e.x,t.x),this.y=ut(this.y,e.y,t.y),this}clampScalar(e,t){return this.x=ut(this.x,e,t),this.y=ut(this.y,e,t),this}clampLength(e,t){let n=this.length();return this.divideScalar(n||1).multiplyScalar(ut(n,e,t))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this}negate(){return this.x=-this.x,this.y=-this.y,this}dot(e){return this.x*e.x+this.y*e.y}cross(e){return this.x*e.y-this.y*e.x}lengthSq(){return this.x*this.x+this.y*this.y}length(){return Math.sqrt(this.x*this.x+this.y*this.y)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)}normalize(){return this.divideScalar(this.length()||1)}angle(){return Math.atan2(-this.y,-this.x)+Math.PI}angleTo(e){let t=Math.sqrt(this.lengthSq()*e.lengthSq());if(t===0)return Math.PI/2;let n=this.dot(e)/t;return Math.acos(ut(n,-1,1))}distanceTo(e){return Math.sqrt(this.distanceToSquared(e))}distanceToSquared(e){let t=this.x-e.x,n=this.y-e.y;return t*t+n*n}manhattanDistanceTo(e){return Math.abs(this.x-e.x)+Math.abs(this.y-e.y)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,t){return this.x+=(e.x-this.x)*t,this.y+=(e.y-this.y)*t,this}lerpVectors(e,t,n){return this.x=e.x+(t.x-e.x)*n,this.y=e.y+(t.y-e.y)*n,this}equals(e){return e.x===this.x&&e.y===this.y}fromArray(e,t=0){return this.x=e[t],this.y=e[t+1],this}toArray(e=[],t=0){return e[t]=this.x,e[t+1]=this.y,e}fromBufferAttribute(e,t){return this.x=e.getX(t),this.y=e.getY(t),this}rotateAround(e,t){let n=Math.cos(t),i=Math.sin(t),s=this.x-e.x,a=this.y-e.y;return this.x=s*n-a*i+e.x,this.y=s*i+a*n+e.y,this}random(){return this.x=Math.random(),this.y=Math.random(),this}*[Symbol.iterator](){yield this.x,yield this.y}},Bt=class{constructor(e=0,t=0,n=0,i=1){this.isQuaternion=!0,this._x=e,this._y=t,this._z=n,this._w=i}static slerpFlat(e,t,n,i,s,a,o){let c=n[i+0],l=n[i+1],h=n[i+2],p=n[i+3],f=s[a+0],m=s[a+1],v=s[a+2],g=s[a+3];if(p!==g||c!==f||l!==m||h!==v){let u=c*f+l*m+h*v+p*g;u<0&&(f=-f,m=-m,v=-v,g=-g,u=-u);let y=1-o;if(u<.9995){let x=Math.acos(u),S=Math.sin(x);y=Math.sin(y*x)/S,c=c*y+f*(o=Math.sin(o*x)/S),l=l*y+m*o,h=h*y+v*o,p=p*y+g*o}else{c=c*y+f*o,l=l*y+m*o,h=h*y+v*o,p=p*y+g*o;let x=1/Math.sqrt(c*c+l*l+h*h+p*p);c*=x,l*=x,h*=x,p*=x}}e[t]=c,e[t+1]=l,e[t+2]=h,e[t+3]=p}static multiplyQuaternionsFlat(e,t,n,i,s,a){let o=n[i],c=n[i+1],l=n[i+2],h=n[i+3],p=s[a],f=s[a+1],m=s[a+2],v=s[a+3];return e[t]=o*v+h*p+c*m-l*f,e[t+1]=c*v+h*f+l*p-o*m,e[t+2]=l*v+h*m+o*f-c*p,e[t+3]=h*v-o*p-c*f-l*m,e}get x(){return this._x}set x(e){this._x=e,this._onChangeCallback()}get y(){return this._y}set y(e){this._y=e,this._onChangeCallback()}get z(){return this._z}set z(e){this._z=e,this._onChangeCallback()}get w(){return this._w}set w(e){this._w=e,this._onChangeCallback()}set(e,t,n,i){return this._x=e,this._y=t,this._z=n,this._w=i,this._onChangeCallback(),this}clone(){return new this.constructor(this._x,this._y,this._z,this._w)}copy(e){return this._x=e.x,this._y=e.y,this._z=e.z,this._w=e.w,this._onChangeCallback(),this}setFromEuler(e,t=!0){let n=e._x,i=e._y,s=e._z,a=e._order,o=Math.cos,c=Math.sin,l=o(n/2),h=o(i/2),p=o(s/2),f=c(n/2),m=c(i/2),v=c(s/2);switch(a){case"XYZ":this._x=f*h*p+l*m*v,this._y=l*m*p-f*h*v,this._z=l*h*v+f*m*p,this._w=l*h*p-f*m*v;break;case"YXZ":this._x=f*h*p+l*m*v,this._y=l*m*p-f*h*v,this._z=l*h*v-f*m*p,this._w=l*h*p+f*m*v;break;case"ZXY":this._x=f*h*p-l*m*v,this._y=l*m*p+f*h*v,this._z=l*h*v+f*m*p,this._w=l*h*p-f*m*v;break;case"ZYX":this._x=f*h*p-l*m*v,this._y=l*m*p+f*h*v,this._z=l*h*v-f*m*p,this._w=l*h*p+f*m*v;break;case"YZX":this._x=f*h*p+l*m*v,this._y=l*m*p+f*h*v,this._z=l*h*v-f*m*p,this._w=l*h*p-f*m*v;break;case"XZY":this._x=f*h*p-l*m*v,this._y=l*m*p-f*h*v,this._z=l*h*v+f*m*p,this._w=l*h*p+f*m*v;break;default:tt("Quaternion: .setFromEuler() encountered an unknown order: "+a)}return t===!0&&this._onChangeCallback(),this}setFromAxisAngle(e,t){let n=t/2,i=Math.sin(n);return this._x=e.x*i,this._y=e.y*i,this._z=e.z*i,this._w=Math.cos(n),this._onChangeCallback(),this}setFromRotationMatrix(e){let t=e.elements,n=t[0],i=t[4],s=t[8],a=t[1],o=t[5],c=t[9],l=t[2],h=t[6],p=t[10],f=n+o+p;if(f>0){let m=.5/Math.sqrt(f+1);this._w=.25/m,this._x=(h-c)*m,this._y=(s-l)*m,this._z=(a-i)*m}else if(n>o&&n>p){let m=2*Math.sqrt(1+n-o-p);this._w=(h-c)/m,this._x=.25*m,this._y=(i+a)/m,this._z=(s+l)/m}else if(o>p){let m=2*Math.sqrt(1+o-n-p);this._w=(s-l)/m,this._x=(i+a)/m,this._y=.25*m,this._z=(c+h)/m}else{let m=2*Math.sqrt(1+p-n-o);this._w=(a-i)/m,this._x=(s+l)/m,this._y=(c+h)/m,this._z=.25*m}return this._onChangeCallback(),this}setFromUnitVectors(e,t){let n=e.dot(t)+1;return n<1e-8?(n=0,Math.abs(e.x)>Math.abs(e.z)?(this._x=-e.y,this._y=e.x,this._z=0,this._w=n):(this._x=0,this._y=-e.z,this._z=e.y,this._w=n)):(this._x=e.y*t.z-e.z*t.y,this._y=e.z*t.x-e.x*t.z,this._z=e.x*t.y-e.y*t.x,this._w=n),this.normalize()}angleTo(e){return 2*Math.acos(Math.abs(ut(this.dot(e),-1,1)))}rotateTowards(e,t){let n=this.angleTo(e);if(n===0)return this;let i=Math.min(1,t/n);return this.slerp(e,i),this}identity(){return this.set(0,0,0,1)}invert(){return this.conjugate()}conjugate(){return this._x*=-1,this._y*=-1,this._z*=-1,this._onChangeCallback(),this}dot(e){return this._x*e._x+this._y*e._y+this._z*e._z+this._w*e._w}lengthSq(){return this._x*this._x+this._y*this._y+this._z*this._z+this._w*this._w}length(){return Math.sqrt(this._x*this._x+this._y*this._y+this._z*this._z+this._w*this._w)}normalize(){let e=this.length();return e===0?(this._x=0,this._y=0,this._z=0,this._w=1):(e=1/e,this._x=this._x*e,this._y=this._y*e,this._z=this._z*e,this._w=this._w*e),this._onChangeCallback(),this}multiply(e){return this.multiplyQuaternions(this,e)}premultiply(e){return this.multiplyQuaternions(e,this)}multiplyQuaternions(e,t){let n=e._x,i=e._y,s=e._z,a=e._w,o=t._x,c=t._y,l=t._z,h=t._w;return this._x=n*h+a*o+i*l-s*c,this._y=i*h+a*c+s*o-n*l,this._z=s*h+a*l+n*c-i*o,this._w=a*h-n*o-i*c-s*l,this._onChangeCallback(),this}slerp(e,t){let n=e._x,i=e._y,s=e._z,a=e._w,o=this.dot(e);o<0&&(n=-n,i=-i,s=-s,a=-a,o=-o);let c=1-t;if(o<.9995){let l=Math.acos(o),h=Math.sin(l);c=Math.sin(c*l)/h,t=Math.sin(t*l)/h,this._x=this._x*c+n*t,this._y=this._y*c+i*t,this._z=this._z*c+s*t,this._w=this._w*c+a*t,this._onChangeCallback()}else this._x=this._x*c+n*t,this._y=this._y*c+i*t,this._z=this._z*c+s*t,this._w=this._w*c+a*t,this.normalize();return this}slerpQuaternions(e,t,n){return this.copy(e).slerp(t,n)}random(){let e=2*Math.PI*Math.random(),t=2*Math.PI*Math.random(),n=Math.random(),i=Math.sqrt(1-n),s=Math.sqrt(n);return this.set(i*Math.sin(e),i*Math.cos(e),s*Math.sin(t),s*Math.cos(t))}equals(e){return e._x===this._x&&e._y===this._y&&e._z===this._z&&e._w===this._w}fromArray(e,t=0){return this._x=e[t],this._y=e[t+1],this._z=e[t+2],this._w=e[t+3],this._onChangeCallback(),this}toArray(e=[],t=0){return e[t]=this._x,e[t+1]=this._y,e[t+2]=this._z,e[t+3]=this._w,e}fromBufferAttribute(e,t){return this._x=e.getX(t),this._y=e.getY(t),this._z=e.getZ(t),this._w=e.getW(t),this._onChangeCallback(),this}toJSON(){return this.toArray()}_onChange(e){return this._onChangeCallback=e,this}_onChangeCallback(){}*[Symbol.iterator](){yield this._x,yield this._y,yield this._z,yield this._w}},N=class r{constructor(e=0,t=0,n=0){r.prototype.isVector3=!0,this.x=e,this.y=t,this.z=n}set(e,t,n){return n===void 0&&(n=this.z),this.x=e,this.y=t,this.z=n,this}setScalar(e){return this.x=e,this.y=e,this.z=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setZ(e){return this.z=e,this}setComponent(e,t){switch(e){case 0:this.x=t;break;case 1:this.y=t;break;case 2:this.z=t;break;default:throw new Error("index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;case 2:return this.z;default:throw new Error("index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y,this.z)}copy(e){return this.x=e.x,this.y=e.y,this.z=e.z,this}add(e){return this.x+=e.x,this.y+=e.y,this.z+=e.z,this}addScalar(e){return this.x+=e,this.y+=e,this.z+=e,this}addVectors(e,t){return this.x=e.x+t.x,this.y=e.y+t.y,this.z=e.z+t.z,this}addScaledVector(e,t){return this.x+=e.x*t,this.y+=e.y*t,this.z+=e.z*t,this}sub(e){return this.x-=e.x,this.y-=e.y,this.z-=e.z,this}subScalar(e){return this.x-=e,this.y-=e,this.z-=e,this}subVectors(e,t){return this.x=e.x-t.x,this.y=e.y-t.y,this.z=e.z-t.z,this}multiply(e){return this.x*=e.x,this.y*=e.y,this.z*=e.z,this}multiplyScalar(e){return this.x*=e,this.y*=e,this.z*=e,this}multiplyVectors(e,t){return this.x=e.x*t.x,this.y=e.y*t.y,this.z=e.z*t.z,this}applyEuler(e){return this.applyQuaternion(Id.setFromEuler(e))}applyAxisAngle(e,t){return this.applyQuaternion(Id.setFromAxisAngle(e,t))}applyMatrix3(e){let t=this.x,n=this.y,i=this.z,s=e.elements;return this.x=s[0]*t+s[3]*n+s[6]*i,this.y=s[1]*t+s[4]*n+s[7]*i,this.z=s[2]*t+s[5]*n+s[8]*i,this}applyNormalMatrix(e){return this.applyMatrix3(e).normalize()}applyMatrix4(e){let t=this.x,n=this.y,i=this.z,s=e.elements,a=1/(s[3]*t+s[7]*n+s[11]*i+s[15]);return this.x=(s[0]*t+s[4]*n+s[8]*i+s[12])*a,this.y=(s[1]*t+s[5]*n+s[9]*i+s[13])*a,this.z=(s[2]*t+s[6]*n+s[10]*i+s[14])*a,this}applyQuaternion(e){let t=this.x,n=this.y,i=this.z,s=e.x,a=e.y,o=e.z,c=e.w,l=2*(a*i-o*n),h=2*(o*t-s*i),p=2*(s*n-a*t);return this.x=t+c*l+a*p-o*h,this.y=n+c*h+o*l-s*p,this.z=i+c*p+s*h-a*l,this}project(e){return this.applyMatrix4(e.matrixWorldInverse).applyMatrix4(e.projectionMatrix)}unproject(e){return this.applyMatrix4(e.projectionMatrixInverse).applyMatrix4(e.matrixWorld)}transformDirection(e){let t=this.x,n=this.y,i=this.z,s=e.elements;return this.x=s[0]*t+s[4]*n+s[8]*i,this.y=s[1]*t+s[5]*n+s[9]*i,this.z=s[2]*t+s[6]*n+s[10]*i,this.normalize()}divide(e){return this.x/=e.x,this.y/=e.y,this.z/=e.z,this}divideScalar(e){return this.multiplyScalar(1/e)}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this.z=Math.min(this.z,e.z),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this.z=Math.max(this.z,e.z),this}clamp(e,t){return this.x=ut(this.x,e.x,t.x),this.y=ut(this.y,e.y,t.y),this.z=ut(this.z,e.z,t.z),this}clampScalar(e,t){return this.x=ut(this.x,e,t),this.y=ut(this.y,e,t),this.z=ut(this.z,e,t),this}clampLength(e,t){let n=this.length();return this.divideScalar(n||1).multiplyScalar(ut(n,e,t))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this.z=Math.floor(this.z),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this.z=Math.ceil(this.z),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this.z=Math.round(this.z),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this.z=Math.trunc(this.z),this}negate(){return this.x=-this.x,this.y=-this.y,this.z=-this.z,this}dot(e){return this.x*e.x+this.y*e.y+this.z*e.z}lengthSq(){return this.x*this.x+this.y*this.y+this.z*this.z}length(){return Math.sqrt(this.x*this.x+this.y*this.y+this.z*this.z)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)+Math.abs(this.z)}normalize(){return this.divideScalar(this.length()||1)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,t){return this.x+=(e.x-this.x)*t,this.y+=(e.y-this.y)*t,this.z+=(e.z-this.z)*t,this}lerpVectors(e,t,n){return this.x=e.x+(t.x-e.x)*n,this.y=e.y+(t.y-e.y)*n,this.z=e.z+(t.z-e.z)*n,this}cross(e){return this.crossVectors(this,e)}crossVectors(e,t){let n=e.x,i=e.y,s=e.z,a=t.x,o=t.y,c=t.z;return this.x=i*c-s*o,this.y=s*a-n*c,this.z=n*o-i*a,this}projectOnVector(e){let t=e.lengthSq();if(t===0)return this.set(0,0,0);let n=e.dot(this)/t;return this.copy(e).multiplyScalar(n)}projectOnPlane(e){return Wc.copy(this).projectOnVector(e),this.sub(Wc)}reflect(e){return this.sub(Wc.copy(e).multiplyScalar(2*this.dot(e)))}angleTo(e){let t=Math.sqrt(this.lengthSq()*e.lengthSq());if(t===0)return Math.PI/2;let n=this.dot(e)/t;return Math.acos(ut(n,-1,1))}distanceTo(e){return Math.sqrt(this.distanceToSquared(e))}distanceToSquared(e){let t=this.x-e.x,n=this.y-e.y,i=this.z-e.z;return t*t+n*n+i*i}manhattanDistanceTo(e){return Math.abs(this.x-e.x)+Math.abs(this.y-e.y)+Math.abs(this.z-e.z)}setFromSpherical(e){return this.setFromSphericalCoords(e.radius,e.phi,e.theta)}setFromSphericalCoords(e,t,n){let i=Math.sin(t)*e;return this.x=i*Math.sin(n),this.y=Math.cos(t)*e,this.z=i*Math.cos(n),this}setFromCylindrical(e){return this.setFromCylindricalCoords(e.radius,e.theta,e.y)}setFromCylindricalCoords(e,t,n){return this.x=e*Math.sin(t),this.y=n,this.z=e*Math.cos(t),this}setFromMatrixPosition(e){let t=e.elements;return this.x=t[12],this.y=t[13],this.z=t[14],this}setFromMatrixScale(e){let t=this.setFromMatrixColumn(e,0).length(),n=this.setFromMatrixColumn(e,1).length(),i=this.setFromMatrixColumn(e,2).length();return this.x=t,this.y=n,this.z=i,this}setFromMatrixColumn(e,t){return this.fromArray(e.elements,4*t)}setFromMatrix3Column(e,t){return this.fromArray(e.elements,3*t)}setFromEuler(e){return this.x=e._x,this.y=e._y,this.z=e._z,this}setFromColor(e){return this.x=e.r,this.y=e.g,this.z=e.b,this}equals(e){return e.x===this.x&&e.y===this.y&&e.z===this.z}fromArray(e,t=0){return this.x=e[t],this.y=e[t+1],this.z=e[t+2],this}toArray(e=[],t=0){return e[t]=this.x,e[t+1]=this.y,e[t+2]=this.z,e}fromBufferAttribute(e,t){return this.x=e.getX(t),this.y=e.getY(t),this.z=e.getZ(t),this}random(){return this.x=Math.random(),this.y=Math.random(),this.z=Math.random(),this}randomDirection(){let e=Math.random()*Math.PI*2,t=2*Math.random()-1,n=Math.sqrt(1-t*t);return this.x=n*Math.cos(e),this.y=t,this.z=n*Math.sin(e),this}*[Symbol.iterator](){yield this.x,yield this.y,yield this.z}},Wc=new N,Id=new Bt,ht=class r{constructor(e,t,n,i,s,a,o,c,l){r.prototype.isMatrix3=!0,this.elements=[1,0,0,0,1,0,0,0,1],e!==void 0&&this.set(e,t,n,i,s,a,o,c,l)}set(e,t,n,i,s,a,o,c,l){let h=this.elements;return h[0]=e,h[1]=i,h[2]=o,h[3]=t,h[4]=s,h[5]=c,h[6]=n,h[7]=a,h[8]=l,this}identity(){return this.set(1,0,0,0,1,0,0,0,1),this}copy(e){let t=this.elements,n=e.elements;return t[0]=n[0],t[1]=n[1],t[2]=n[2],t[3]=n[3],t[4]=n[4],t[5]=n[5],t[6]=n[6],t[7]=n[7],t[8]=n[8],this}extractBasis(e,t,n){return e.setFromMatrix3Column(this,0),t.setFromMatrix3Column(this,1),n.setFromMatrix3Column(this,2),this}setFromMatrix4(e){let t=e.elements;return this.set(t[0],t[4],t[8],t[1],t[5],t[9],t[2],t[6],t[10]),this}multiply(e){return this.multiplyMatrices(this,e)}premultiply(e){return this.multiplyMatrices(e,this)}multiplyMatrices(e,t){let n=e.elements,i=t.elements,s=this.elements,a=n[0],o=n[3],c=n[6],l=n[1],h=n[4],p=n[7],f=n[2],m=n[5],v=n[8],g=i[0],u=i[3],y=i[6],x=i[1],S=i[4],E=i[7],R=i[2],P=i[5],V=i[8];return s[0]=a*g+o*x+c*R,s[3]=a*u+o*S+c*P,s[6]=a*y+o*E+c*V,s[1]=l*g+h*x+p*R,s[4]=l*u+h*S+p*P,s[7]=l*y+h*E+p*V,s[2]=f*g+m*x+v*R,s[5]=f*u+m*S+v*P,s[8]=f*y+m*E+v*V,this}multiplyScalar(e){let t=this.elements;return t[0]*=e,t[3]*=e,t[6]*=e,t[1]*=e,t[4]*=e,t[7]*=e,t[2]*=e,t[5]*=e,t[8]*=e,this}determinant(){let e=this.elements,t=e[0],n=e[1],i=e[2],s=e[3],a=e[4],o=e[5],c=e[6],l=e[7],h=e[8];return t*a*h-t*o*l-n*s*h+n*o*c+i*s*l-i*a*c}invert(){let e=this.elements,t=e[0],n=e[1],i=e[2],s=e[3],a=e[4],o=e[5],c=e[6],l=e[7],h=e[8],p=h*a-o*l,f=o*c-h*s,m=l*s-a*c,v=t*p+n*f+i*m;if(v===0)return this.set(0,0,0,0,0,0,0,0,0);let g=1/v;return e[0]=p*g,e[1]=(i*l-h*n)*g,e[2]=(o*n-i*a)*g,e[3]=f*g,e[4]=(h*t-i*c)*g,e[5]=(i*s-o*t)*g,e[6]=m*g,e[7]=(n*c-l*t)*g,e[8]=(a*t-n*s)*g,this}transpose(){let e,t=this.elements;return e=t[1],t[1]=t[3],t[3]=e,e=t[2],t[2]=t[6],t[6]=e,e=t[5],t[5]=t[7],t[7]=e,this}getNormalMatrix(e){return this.setFromMatrix4(e).invert().transpose()}transposeIntoArray(e){let t=this.elements;return e[0]=t[0],e[1]=t[3],e[2]=t[6],e[3]=t[1],e[4]=t[4],e[5]=t[7],e[6]=t[2],e[7]=t[5],e[8]=t[8],this}setUvTransform(e,t,n,i,s,a,o){let c=Math.cos(s),l=Math.sin(s);return this.set(n*c,n*l,-n*(c*a+l*o)+a+e,-i*l,i*c,-i*(-l*a+c*o)+o+t,0,0,1),this}scale(e,t){return this.premultiply(jc.makeScale(e,t)),this}rotate(e){return this.premultiply(jc.makeRotation(-e)),this}translate(e,t){return this.premultiply(jc.makeTranslation(e,t)),this}makeTranslation(e,t){return e.isVector2?this.set(1,0,e.x,0,1,e.y,0,0,1):this.set(1,0,e,0,1,t,0,0,1),this}makeRotation(e){let t=Math.cos(e),n=Math.sin(e);return this.set(t,-n,0,n,t,0,0,0,1),this}makeScale(e,t){return this.set(e,0,0,0,t,0,0,0,1),this}equals(e){let t=this.elements,n=e.elements;for(let i=0;i<9;i++)if(t[i]!==n[i])return!1;return!0}fromArray(e,t=0){for(let n=0;n<9;n++)this.elements[n]=e[n+t];return this}toArray(e=[],t=0){let n=this.elements;return e[t]=n[0],e[t+1]=n[1],e[t+2]=n[2],e[t+3]=n[3],e[t+4]=n[4],e[t+5]=n[5],e[t+6]=n[6],e[t+7]=n[7],e[t+8]=n[8],e}clone(){return new this.constructor().fromArray(this.elements)}},jc=new ht,Ld=new ht().set(.4123908,.3575843,.1804808,.212639,.7151687,.0721923,.0193308,.1191948,.9505322),Dd=new ht().set(3.2409699,-1.5373832,-.4986108,-.9692436,1.8759675,.0415551,.0556301,-.203977,1.0569715);function hm(){let r={enabled:!0,workingColorSpace:Rr,spaces:{},convert:function(i,s,a){return this.enabled!==!1&&s!==a&&s&&a&&(this.spaces[s].transfer===bt&&(i.r=ji(i.r),i.g=ji(i.g),i.b=ji(i.b)),this.spaces[s].primaries!==this.spaces[a].primaries&&(i.applyMatrix3(this.spaces[s].toXYZ),i.applyMatrix3(this.spaces[a].fromXYZ)),this.spaces[a].transfer===bt&&(i.r=fs(i.r),i.g=fs(i.g),i.b=fs(i.b))),i},workingToColorSpace:function(i,s){return this.convert(i,this.workingColorSpace,s)},colorSpaceToWorking:function(i,s){return this.convert(i,s,this.workingColorSpace)},getPrimaries:function(i){return this.spaces[i].primaries},getTransfer:function(i){return i===""?va:this.spaces[i].transfer},getToneMappingMode:function(i){return this.spaces[i].outputColorSpaceConfig.toneMappingMode||"standard"},getLuminanceCoefficients:function(i,s=this.workingColorSpace){return i.fromArray(this.spaces[s].luminanceCoefficients)},define:function(i){Object.assign(this.spaces,i)},_getMatrix:function(i,s,a){return i.copy(this.spaces[s].toXYZ).multiply(this.spaces[a].fromXYZ)},_getDrawingBufferColorSpace:function(i){return this.spaces[i].outputColorSpaceConfig.drawingBufferColorSpace},_getUnpackColorSpace:function(i=this.workingColorSpace){return this.spaces[i].workingColorSpaceConfig.unpackColorSpace},fromWorkingColorSpace:function(i,s){return ya("ColorManagement: .fromWorkingColorSpace() has been renamed to .workingToColorSpace()."),r.workingToColorSpace(i,s)},toWorkingColorSpace:function(i,s){return ya("ColorManagement: .toWorkingColorSpace() has been renamed to .colorSpaceToWorking()."),r.colorSpaceToWorking(i,s)}},e=[.64,.33,.3,.6,.15,.06],t=[.2126,.7152,.0722],n=[.3127,.329];return r.define({[Rr]:{primaries:e,whitePoint:n,transfer:va,toXYZ:Ld,fromXYZ:Dd,luminanceCoefficients:t,workingColorSpaceConfig:{unpackColorSpace:Xt},outputColorSpaceConfig:{drawingBufferColorSpace:Xt}},[Xt]:{primaries:e,whitePoint:n,transfer:bt,toXYZ:Ld,fromXYZ:Dd,luminanceCoefficients:t,outputColorSpaceConfig:{drawingBufferColorSpace:Xt}}}),r}var Mt=hm();function ji(r){return r<.04045?.0773993808*r:Math.pow(.9478672986*r+.0521327014,2.4)}function fs(r){return r<.0031308?12.92*r:1.055*Math.pow(r,.41666)-.055}var Qr,sl=class{static getDataURL(e,t="image/png"){if(/^data:/i.test(e.src)||typeof HTMLCanvasElement>"u")return e.src;let n;if(e instanceof HTMLCanvasElement)n=e;else{Qr===void 0&&(Qr=_s("canvas")),Qr.width=e.width,Qr.height=e.height;let i=Qr.getContext("2d");e instanceof ImageData?i.putImageData(e,0,0):i.drawImage(e,0,0,e.width,e.height),n=Qr}return n.toDataURL(t)}static sRGBToLinear(e){if(typeof HTMLImageElement<"u"&&e instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&e instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&e instanceof ImageBitmap){let t=_s("canvas");t.width=e.width,t.height=e.height;let n=t.getContext("2d");n.drawImage(e,0,0,e.width,e.height);let i=n.getImageData(0,0,e.width,e.height),s=i.data;for(let a=0;a<s.length;a++)s[a]=255*ji(s[a]/255);return n.putImageData(i,0,0),t}if(e.data){let t=e.data.slice(0);for(let n=0;n<t.length;n++)t instanceof Uint8Array||t instanceof Uint8ClampedArray?t[n]=Math.floor(255*ji(t[n]/255)):t[n]=ji(t[n]);return{data:t,width:e.width,height:e.height}}return tt("ImageUtils.sRGBToLinear(): Unsupported image type. No color space conversion applied."),e}},um=0,xs=class{constructor(e=null){this.isSource=!0,Object.defineProperty(this,"id",{value:um++}),this.uuid=Br(),this.data=e,this.dataReady=!0,this.version=0}getSize(e){let t=this.data;return typeof HTMLVideoElement<"u"&&t instanceof HTMLVideoElement?e.set(t.videoWidth,t.videoHeight,0):typeof VideoFrame<"u"&&t instanceof VideoFrame?e.set(t.displayHeight,t.displayWidth,0):t!==null?e.set(t.width,t.height,t.depth||0):e.set(0,0,0),e}set needsUpdate(e){e===!0&&this.version++}toJSON(e){let t=e===void 0||typeof e=="string";if(!t&&e.images[this.uuid]!==void 0)return e.images[this.uuid];let n={uuid:this.uuid,url:""},i=this.data;if(i!==null){let s;if(Array.isArray(i)){s=[];for(let a=0,o=i.length;a<o;a++)i[a].isDataTexture?s.push(Xc(i[a].image)):s.push(Xc(i[a]))}else s=Xc(i);n.url=s}return t||(e.images[this.uuid]=n),n}};function Xc(r){return typeof HTMLImageElement<"u"&&r instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&r instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&r instanceof ImageBitmap?sl.getDataURL(r):r.data?{data:Array.from(r.data),width:r.width,height:r.height,type:r.data.constructor.name}:(tt("Texture: Unable to serialize Texture."),{})}var dm=0,qc=new N,_n=class r extends bi{constructor(e=r.DEFAULT_IMAGE,t=r.DEFAULT_MAPPING,n=1001,i=1001,s=1006,a=1008,o=1023,c=1009,l=r.DEFAULT_ANISOTROPY,h=""){super(),this.isTexture=!0,Object.defineProperty(this,"id",{value:dm++}),this.uuid=Br(),this.name="",this.source=new xs(e),this.mipmaps=[],this.mapping=t,this.channel=0,this.wrapS=n,this.wrapT=i,this.magFilter=s,this.minFilter=a,this.anisotropy=l,this.format=o,this.internalFormat=null,this.type=c,this.offset=new Te(0,0),this.repeat=new Te(1,1),this.center=new Te(0,0),this.rotation=0,this.matrixAutoUpdate=!0,this.matrix=new ht,this.generateMipmaps=!0,this.premultiplyAlpha=!1,this.flipY=!0,this.unpackAlignment=4,this.colorSpace=h,this.userData={},this.updateRanges=[],this.version=0,this.onUpdate=null,this.renderTarget=null,this.isRenderTargetTexture=!1,this.isArrayTexture=!!(e&&e.depth&&e.depth>1),this.pmremVersion=0}get width(){return this.source.getSize(qc).x}get height(){return this.source.getSize(qc).y}get depth(){return this.source.getSize(qc).z}get image(){return this.source.data}set image(e=null){this.source.data=e}updateMatrix(){this.matrix.setUvTransform(this.offset.x,this.offset.y,this.repeat.x,this.repeat.y,this.rotation,this.center.x,this.center.y)}addUpdateRange(e,t){this.updateRanges.push({start:e,count:t})}clearUpdateRanges(){this.updateRanges.length=0}clone(){return new this.constructor().copy(this)}copy(e){return this.name=e.name,this.source=e.source,this.mipmaps=e.mipmaps.slice(0),this.mapping=e.mapping,this.channel=e.channel,this.wrapS=e.wrapS,this.wrapT=e.wrapT,this.magFilter=e.magFilter,this.minFilter=e.minFilter,this.anisotropy=e.anisotropy,this.format=e.format,this.internalFormat=e.internalFormat,this.type=e.type,this.offset.copy(e.offset),this.repeat.copy(e.repeat),this.center.copy(e.center),this.rotation=e.rotation,this.matrixAutoUpdate=e.matrixAutoUpdate,this.matrix.copy(e.matrix),this.generateMipmaps=e.generateMipmaps,this.premultiplyAlpha=e.premultiplyAlpha,this.flipY=e.flipY,this.unpackAlignment=e.unpackAlignment,this.colorSpace=e.colorSpace,this.renderTarget=e.renderTarget,this.isRenderTargetTexture=e.isRenderTargetTexture,this.isArrayTexture=e.isArrayTexture,this.userData=JSON.parse(JSON.stringify(e.userData)),this.needsUpdate=!0,this}setValues(e){for(let t in e){let n=e[t];if(n===void 0){tt(`Texture.setValues(): parameter '${t}' has value of undefined.`);continue}let i=this[t];i!==void 0?i&&n&&i.isVector2&&n.isVector2||i&&n&&i.isVector3&&n.isVector3||i&&n&&i.isMatrix3&&n.isMatrix3?i.copy(n):this[t]=n:tt(`Texture.setValues(): property '${t}' does not exist.`)}}toJSON(e){let t=e===void 0||typeof e=="string";if(!t&&e.textures[this.uuid]!==void 0)return e.textures[this.uuid];let n={metadata:{version:4.7,type:"Texture",generator:"Texture.toJSON"},uuid:this.uuid,name:this.name,image:this.source.toJSON(e).uuid,mapping:this.mapping,channel:this.channel,repeat:[this.repeat.x,this.repeat.y],offset:[this.offset.x,this.offset.y],center:[this.center.x,this.center.y],rotation:this.rotation,wrap:[this.wrapS,this.wrapT],format:this.format,internalFormat:this.internalFormat,type:this.type,colorSpace:this.colorSpace,minFilter:this.minFilter,magFilter:this.magFilter,anisotropy:this.anisotropy,flipY:this.flipY,generateMipmaps:this.generateMipmaps,premultiplyAlpha:this.premultiplyAlpha,unpackAlignment:this.unpackAlignment};return Object.keys(this.userData).length>0&&(n.userData=this.userData),t||(e.textures[this.uuid]=n),n}dispose(){this.dispatchEvent({type:"dispose"})}transformUv(e){if(this.mapping!==Yh)return e;if(e.applyMatrix3(this.matrix),e.x<0||e.x>1)switch(this.wrapS){case ms:e.x=e.x-Math.floor(e.x);break;case ar:e.x=e.x<0?0:1;break;case il:Math.abs(Math.floor(e.x)%2)===1?e.x=Math.ceil(e.x)-e.x:e.x=e.x-Math.floor(e.x)}if(e.y<0||e.y>1)switch(this.wrapT){case ms:e.y=e.y-Math.floor(e.y);break;case ar:e.y=e.y<0?0:1;break;case il:Math.abs(Math.floor(e.y)%2)===1?e.y=Math.ceil(e.y)-e.y:e.y=e.y-Math.floor(e.y)}return this.flipY&&(e.y=1-e.y),e}set needsUpdate(e){e===!0&&(this.version++,this.source.needsUpdate=!0)}set needsPMREMUpdate(e){e===!0&&this.pmremVersion++}};_n.DEFAULT_IMAGE=null,_n.DEFAULT_MAPPING=Yh,_n.DEFAULT_ANISOTROPY=1;var Lt=class r{constructor(e=0,t=0,n=0,i=1){r.prototype.isVector4=!0,this.x=e,this.y=t,this.z=n,this.w=i}get width(){return this.z}set width(e){this.z=e}get height(){return this.w}set height(e){this.w=e}set(e,t,n,i){return this.x=e,this.y=t,this.z=n,this.w=i,this}setScalar(e){return this.x=e,this.y=e,this.z=e,this.w=e,this}setX(e){return this.x=e,this}setY(e){return this.y=e,this}setZ(e){return this.z=e,this}setW(e){return this.w=e,this}setComponent(e,t){switch(e){case 0:this.x=t;break;case 1:this.y=t;break;case 2:this.z=t;break;case 3:this.w=t;break;default:throw new Error("index is out of range: "+e)}return this}getComponent(e){switch(e){case 0:return this.x;case 1:return this.y;case 2:return this.z;case 3:return this.w;default:throw new Error("index is out of range: "+e)}}clone(){return new this.constructor(this.x,this.y,this.z,this.w)}copy(e){return this.x=e.x,this.y=e.y,this.z=e.z,this.w=e.w!==void 0?e.w:1,this}add(e){return this.x+=e.x,this.y+=e.y,this.z+=e.z,this.w+=e.w,this}addScalar(e){return this.x+=e,this.y+=e,this.z+=e,this.w+=e,this}addVectors(e,t){return this.x=e.x+t.x,this.y=e.y+t.y,this.z=e.z+t.z,this.w=e.w+t.w,this}addScaledVector(e,t){return this.x+=e.x*t,this.y+=e.y*t,this.z+=e.z*t,this.w+=e.w*t,this}sub(e){return this.x-=e.x,this.y-=e.y,this.z-=e.z,this.w-=e.w,this}subScalar(e){return this.x-=e,this.y-=e,this.z-=e,this.w-=e,this}subVectors(e,t){return this.x=e.x-t.x,this.y=e.y-t.y,this.z=e.z-t.z,this.w=e.w-t.w,this}multiply(e){return this.x*=e.x,this.y*=e.y,this.z*=e.z,this.w*=e.w,this}multiplyScalar(e){return this.x*=e,this.y*=e,this.z*=e,this.w*=e,this}applyMatrix4(e){let t=this.x,n=this.y,i=this.z,s=this.w,a=e.elements;return this.x=a[0]*t+a[4]*n+a[8]*i+a[12]*s,this.y=a[1]*t+a[5]*n+a[9]*i+a[13]*s,this.z=a[2]*t+a[6]*n+a[10]*i+a[14]*s,this.w=a[3]*t+a[7]*n+a[11]*i+a[15]*s,this}divide(e){return this.x/=e.x,this.y/=e.y,this.z/=e.z,this.w/=e.w,this}divideScalar(e){return this.multiplyScalar(1/e)}setAxisAngleFromQuaternion(e){this.w=2*Math.acos(e.w);let t=Math.sqrt(1-e.w*e.w);return t<1e-4?(this.x=1,this.y=0,this.z=0):(this.x=e.x/t,this.y=e.y/t,this.z=e.z/t),this}setAxisAngleFromRotationMatrix(e){let t,n,i,s,c=e.elements,l=c[0],h=c[4],p=c[8],f=c[1],m=c[5],v=c[9],g=c[2],u=c[6],y=c[10];if(Math.abs(h-f)<.01&&Math.abs(p-g)<.01&&Math.abs(v-u)<.01){if(Math.abs(h+f)<.1&&Math.abs(p+g)<.1&&Math.abs(v+u)<.1&&Math.abs(l+m+y-3)<.1)return this.set(1,0,0,0),this;t=Math.PI;let S=(l+1)/2,E=(m+1)/2,R=(y+1)/2,P=(h+f)/4,V=(p+g)/4,X=(v+u)/4;return S>E&&S>R?S<.01?(n=0,i=.707106781,s=.707106781):(n=Math.sqrt(S),i=P/n,s=V/n):E>R?E<.01?(n=.707106781,i=0,s=.707106781):(i=Math.sqrt(E),n=P/i,s=X/i):R<.01?(n=.707106781,i=.707106781,s=0):(s=Math.sqrt(R),n=V/s,i=X/s),this.set(n,i,s,t),this}let x=Math.sqrt((u-v)*(u-v)+(p-g)*(p-g)+(f-h)*(f-h));return Math.abs(x)<.001&&(x=1),this.x=(u-v)/x,this.y=(p-g)/x,this.z=(f-h)/x,this.w=Math.acos((l+m+y-1)/2),this}setFromMatrixPosition(e){let t=e.elements;return this.x=t[12],this.y=t[13],this.z=t[14],this.w=t[15],this}min(e){return this.x=Math.min(this.x,e.x),this.y=Math.min(this.y,e.y),this.z=Math.min(this.z,e.z),this.w=Math.min(this.w,e.w),this}max(e){return this.x=Math.max(this.x,e.x),this.y=Math.max(this.y,e.y),this.z=Math.max(this.z,e.z),this.w=Math.max(this.w,e.w),this}clamp(e,t){return this.x=ut(this.x,e.x,t.x),this.y=ut(this.y,e.y,t.y),this.z=ut(this.z,e.z,t.z),this.w=ut(this.w,e.w,t.w),this}clampScalar(e,t){return this.x=ut(this.x,e,t),this.y=ut(this.y,e,t),this.z=ut(this.z,e,t),this.w=ut(this.w,e,t),this}clampLength(e,t){let n=this.length();return this.divideScalar(n||1).multiplyScalar(ut(n,e,t))}floor(){return this.x=Math.floor(this.x),this.y=Math.floor(this.y),this.z=Math.floor(this.z),this.w=Math.floor(this.w),this}ceil(){return this.x=Math.ceil(this.x),this.y=Math.ceil(this.y),this.z=Math.ceil(this.z),this.w=Math.ceil(this.w),this}round(){return this.x=Math.round(this.x),this.y=Math.round(this.y),this.z=Math.round(this.z),this.w=Math.round(this.w),this}roundToZero(){return this.x=Math.trunc(this.x),this.y=Math.trunc(this.y),this.z=Math.trunc(this.z),this.w=Math.trunc(this.w),this}negate(){return this.x=-this.x,this.y=-this.y,this.z=-this.z,this.w=-this.w,this}dot(e){return this.x*e.x+this.y*e.y+this.z*e.z+this.w*e.w}lengthSq(){return this.x*this.x+this.y*this.y+this.z*this.z+this.w*this.w}length(){return Math.sqrt(this.x*this.x+this.y*this.y+this.z*this.z+this.w*this.w)}manhattanLength(){return Math.abs(this.x)+Math.abs(this.y)+Math.abs(this.z)+Math.abs(this.w)}normalize(){return this.divideScalar(this.length()||1)}setLength(e){return this.normalize().multiplyScalar(e)}lerp(e,t){return this.x+=(e.x-this.x)*t,this.y+=(e.y-this.y)*t,this.z+=(e.z-this.z)*t,this.w+=(e.w-this.w)*t,this}lerpVectors(e,t,n){return this.x=e.x+(t.x-e.x)*n,this.y=e.y+(t.y-e.y)*n,this.z=e.z+(t.z-e.z)*n,this.w=e.w+(t.w-e.w)*n,this}equals(e){return e.x===this.x&&e.y===this.y&&e.z===this.z&&e.w===this.w}fromArray(e,t=0){return this.x=e[t],this.y=e[t+1],this.z=e[t+2],this.w=e[t+3],this}toArray(e=[],t=0){return e[t]=this.x,e[t+1]=this.y,e[t+2]=this.z,e[t+3]=this.w,e}fromBufferAttribute(e,t){return this.x=e.getX(t),this.y=e.getY(t),this.z=e.getZ(t),this.w=e.getW(t),this}random(){return this.x=Math.random(),this.y=Math.random(),this.z=Math.random(),this.w=Math.random(),this}*[Symbol.iterator](){yield this.x,yield this.y,yield this.z,yield this.w}},al=class extends bi{constructor(e=1,t=1,n={}){super(),n=Object.assign({generateMipmaps:!1,internalFormat:null,minFilter:nn,depthBuffer:!0,stencilBuffer:!1,resolveDepthBuffer:!0,resolveStencilBuffer:!0,depthTexture:null,samples:0,count:1,depth:1,multiview:!1},n),this.isRenderTarget=!0,this.width=e,this.height=t,this.depth=n.depth,this.scissor=new Lt(0,0,e,t),this.scissorTest=!1,this.viewport=new Lt(0,0,e,t),this.textures=[];let i={width:e,height:t,depth:n.depth},s=new _n(i),a=n.count;for(let o=0;o<a;o++)this.textures[o]=s.clone(),this.textures[o].isRenderTargetTexture=!0,this.textures[o].renderTarget=this;this._setTextureOptions(n),this.depthBuffer=n.depthBuffer,this.stencilBuffer=n.stencilBuffer,this.resolveDepthBuffer=n.resolveDepthBuffer,this.resolveStencilBuffer=n.resolveStencilBuffer,this._depthTexture=null,this.depthTexture=n.depthTexture,this.samples=n.samples,this.multiview=n.multiview}_setTextureOptions(e={}){let t={minFilter:nn,generateMipmaps:!1,flipY:!1,internalFormat:null};e.mapping!==void 0&&(t.mapping=e.mapping),e.wrapS!==void 0&&(t.wrapS=e.wrapS),e.wrapT!==void 0&&(t.wrapT=e.wrapT),e.wrapR!==void 0&&(t.wrapR=e.wrapR),e.magFilter!==void 0&&(t.magFilter=e.magFilter),e.minFilter!==void 0&&(t.minFilter=e.minFilter),e.format!==void 0&&(t.format=e.format),e.type!==void 0&&(t.type=e.type),e.anisotropy!==void 0&&(t.anisotropy=e.anisotropy),e.colorSpace!==void 0&&(t.colorSpace=e.colorSpace),e.flipY!==void 0&&(t.flipY=e.flipY),e.generateMipmaps!==void 0&&(t.generateMipmaps=e.generateMipmaps),e.internalFormat!==void 0&&(t.internalFormat=e.internalFormat);for(let n=0;n<this.textures.length;n++)this.textures[n].setValues(t)}get texture(){return this.textures[0]}set texture(e){this.textures[0]=e}set depthTexture(e){this._depthTexture!==null&&(this._depthTexture.renderTarget=null),e!==null&&(e.renderTarget=this),this._depthTexture=e}get depthTexture(){return this._depthTexture}setSize(e,t,n=1){if(this.width!==e||this.height!==t||this.depth!==n){this.width=e,this.height=t,this.depth=n;for(let i=0,s=this.textures.length;i<s;i++)this.textures[i].image.width=e,this.textures[i].image.height=t,this.textures[i].image.depth=n,this.textures[i].isData3DTexture!==!0&&(this.textures[i].isArrayTexture=this.textures[i].image.depth>1);this.dispose()}this.viewport.set(0,0,e,t),this.scissor.set(0,0,e,t)}clone(){return new this.constructor().copy(this)}copy(e){this.width=e.width,this.height=e.height,this.depth=e.depth,this.scissor.copy(e.scissor),this.scissorTest=e.scissorTest,this.viewport.copy(e.viewport),this.textures.length=0;for(let t=0,n=e.textures.length;t<n;t++){this.textures[t]=e.textures[t].clone(),this.textures[t].isRenderTargetTexture=!0,this.textures[t].renderTarget=this;let i=Object.assign({},e.textures[t].image);this.textures[t].source=new xs(i)}return this.depthBuffer=e.depthBuffer,this.stencilBuffer=e.stencilBuffer,this.resolveDepthBuffer=e.resolveDepthBuffer,this.resolveStencilBuffer=e.resolveStencilBuffer,e.depthTexture!==null&&(this.depthTexture=e.depthTexture.clone()),this.samples=e.samples,this}dispose(){this.dispatchEvent({type:"dispose"})}},Vn=class extends al{constructor(e=1,t=1,n={}){super(e,t,n),this.isWebGLRenderTarget=!0}},xa=class extends _n{constructor(e=null,t=1,n=1,i=1){super(null),this.isDataArrayTexture=!0,this.image={data:e,width:t,height:n,depth:i},this.magFilter=ni,this.minFilter=ni,this.wrapR=ar,this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1,this.layerUpdates=new Set}addLayerUpdate(e){this.layerUpdates.add(e)}clearLayerUpdates(){this.layerUpdates.clear()}};var ol=class extends _n{constructor(e=null,t=1,n=1,i=1){super(null),this.isData3DTexture=!0,this.image={data:e,width:t,height:n,depth:i},this.magFilter=ni,this.minFilter=ni,this.wrapR=ar,this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1}};var st=class r{constructor(e,t,n,i,s,a,o,c,l,h,p,f,m,v,g,u){r.prototype.isMatrix4=!0,this.elements=[1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1],e!==void 0&&this.set(e,t,n,i,s,a,o,c,l,h,p,f,m,v,g,u)}set(e,t,n,i,s,a,o,c,l,h,p,f,m,v,g,u){let y=this.elements;return y[0]=e,y[4]=t,y[8]=n,y[12]=i,y[1]=s,y[5]=a,y[9]=o,y[13]=c,y[2]=l,y[6]=h,y[10]=p,y[14]=f,y[3]=m,y[7]=v,y[11]=g,y[15]=u,this}identity(){return this.set(1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1),this}clone(){return new r().fromArray(this.elements)}copy(e){let t=this.elements,n=e.elements;return t[0]=n[0],t[1]=n[1],t[2]=n[2],t[3]=n[3],t[4]=n[4],t[5]=n[5],t[6]=n[6],t[7]=n[7],t[8]=n[8],t[9]=n[9],t[10]=n[10],t[11]=n[11],t[12]=n[12],t[13]=n[13],t[14]=n[14],t[15]=n[15],this}copyPosition(e){let t=this.elements,n=e.elements;return t[12]=n[12],t[13]=n[13],t[14]=n[14],this}setFromMatrix3(e){let t=e.elements;return this.set(t[0],t[3],t[6],0,t[1],t[4],t[7],0,t[2],t[5],t[8],0,0,0,0,1),this}extractBasis(e,t,n){return this.determinant()===0?(e.set(1,0,0),t.set(0,1,0),n.set(0,0,1),this):(e.setFromMatrixColumn(this,0),t.setFromMatrixColumn(this,1),n.setFromMatrixColumn(this,2),this)}makeBasis(e,t,n){return this.set(e.x,t.x,n.x,0,e.y,t.y,n.y,0,e.z,t.z,n.z,0,0,0,0,1),this}extractRotation(e){if(e.determinant()===0)return this.identity();let t=this.elements,n=e.elements,i=1/es.setFromMatrixColumn(e,0).length(),s=1/es.setFromMatrixColumn(e,1).length(),a=1/es.setFromMatrixColumn(e,2).length();return t[0]=n[0]*i,t[1]=n[1]*i,t[2]=n[2]*i,t[3]=0,t[4]=n[4]*s,t[5]=n[5]*s,t[6]=n[6]*s,t[7]=0,t[8]=n[8]*a,t[9]=n[9]*a,t[10]=n[10]*a,t[11]=0,t[12]=0,t[13]=0,t[14]=0,t[15]=1,this}makeRotationFromEuler(e){let t=this.elements,n=e.x,i=e.y,s=e.z,a=Math.cos(n),o=Math.sin(n),c=Math.cos(i),l=Math.sin(i),h=Math.cos(s),p=Math.sin(s);if(e.order==="XYZ"){let f=a*h,m=a*p,v=o*h,g=o*p;t[0]=c*h,t[4]=-c*p,t[8]=l,t[1]=m+v*l,t[5]=f-g*l,t[9]=-o*c,t[2]=g-f*l,t[6]=v+m*l,t[10]=a*c}else if(e.order==="YXZ"){let f=c*h,m=c*p,v=l*h,g=l*p;t[0]=f+g*o,t[4]=v*o-m,t[8]=a*l,t[1]=a*p,t[5]=a*h,t[9]=-o,t[2]=m*o-v,t[6]=g+f*o,t[10]=a*c}else if(e.order==="ZXY"){let f=c*h,m=c*p,v=l*h,g=l*p;t[0]=f-g*o,t[4]=-a*p,t[8]=v+m*o,t[1]=m+v*o,t[5]=a*h,t[9]=g-f*o,t[2]=-a*l,t[6]=o,t[10]=a*c}else if(e.order==="ZYX"){let f=a*h,m=a*p,v=o*h,g=o*p;t[0]=c*h,t[4]=v*l-m,t[8]=f*l+g,t[1]=c*p,t[5]=g*l+f,t[9]=m*l-v,t[2]=-l,t[6]=o*c,t[10]=a*c}else if(e.order==="YZX"){let f=a*c,m=a*l,v=o*c,g=o*l;t[0]=c*h,t[4]=g-f*p,t[8]=v*p+m,t[1]=p,t[5]=a*h,t[9]=-o*h,t[2]=-l*h,t[6]=m*p+v,t[10]=f-g*p}else if(e.order==="XZY"){let f=a*c,m=a*l,v=o*c,g=o*l;t[0]=c*h,t[4]=-p,t[8]=l*h,t[1]=f*p+g,t[5]=a*h,t[9]=m*p-v,t[2]=v*p-m,t[6]=o*h,t[10]=g*p+f}return t[3]=0,t[7]=0,t[11]=0,t[12]=0,t[13]=0,t[14]=0,t[15]=1,this}makeRotationFromQuaternion(e){return this.compose(pm,e,fm)}lookAt(e,t,n){let i=this.elements;return kn.subVectors(e,t),kn.lengthSq()===0&&(kn.z=1),kn.normalize(),$i.crossVectors(n,kn),$i.lengthSq()===0&&(Math.abs(n.z)===1?kn.x+=1e-4:kn.z+=1e-4,kn.normalize(),$i.crossVectors(n,kn)),$i.normalize(),So.crossVectors(kn,$i),i[0]=$i.x,i[4]=So.x,i[8]=kn.x,i[1]=$i.y,i[5]=So.y,i[9]=kn.y,i[2]=$i.z,i[6]=So.z,i[10]=kn.z,this}multiply(e){return this.multiplyMatrices(this,e)}premultiply(e){return this.multiplyMatrices(e,this)}multiplyMatrices(e,t){let n=e.elements,i=t.elements,s=this.elements,a=n[0],o=n[4],c=n[8],l=n[12],h=n[1],p=n[5],f=n[9],m=n[13],v=n[2],g=n[6],u=n[10],y=n[14],x=n[3],S=n[7],E=n[11],R=n[15],P=i[0],V=i[4],X=i[8],H=i[12],se=i[1],oe=i[5],te=i[9],ne=i[13],j=i[2],J=i[6],ie=i[10],re=i[14],Se=i[3],we=i[7],ze=i[11],Ae=i[15];return s[0]=a*P+o*se+c*j+l*Se,s[4]=a*V+o*oe+c*J+l*we,s[8]=a*X+o*te+c*ie+l*ze,s[12]=a*H+o*ne+c*re+l*Ae,s[1]=h*P+p*se+f*j+m*Se,s[5]=h*V+p*oe+f*J+m*we,s[9]=h*X+p*te+f*ie+m*ze,s[13]=h*H+p*ne+f*re+m*Ae,s[2]=v*P+g*se+u*j+y*Se,s[6]=v*V+g*oe+u*J+y*we,s[10]=v*X+g*te+u*ie+y*ze,s[14]=v*H+g*ne+u*re+y*Ae,s[3]=x*P+S*se+E*j+R*Se,s[7]=x*V+S*oe+E*J+R*we,s[11]=x*X+S*te+E*ie+R*ze,s[15]=x*H+S*ne+E*re+R*Ae,this}multiplyScalar(e){let t=this.elements;return t[0]*=e,t[4]*=e,t[8]*=e,t[12]*=e,t[1]*=e,t[5]*=e,t[9]*=e,t[13]*=e,t[2]*=e,t[6]*=e,t[10]*=e,t[14]*=e,t[3]*=e,t[7]*=e,t[11]*=e,t[15]*=e,this}determinant(){let e=this.elements,t=e[0],n=e[4],i=e[8],s=e[12],a=e[1],o=e[5],c=e[9],l=e[13],h=e[2],p=e[6],f=e[10],m=e[14],v=e[3],g=e[7],u=e[11],y=e[15],x=c*m-l*f,S=o*m-l*p,E=o*f-c*p,R=a*m-l*h,P=a*f-c*h,V=a*p-o*h;return t*(g*x-u*S+y*E)-n*(v*x-u*R+y*P)+i*(v*S-g*R+y*V)-s*(v*E-g*P+u*V)}transpose(){let e=this.elements,t;return t=e[1],e[1]=e[4],e[4]=t,t=e[2],e[2]=e[8],e[8]=t,t=e[6],e[6]=e[9],e[9]=t,t=e[3],e[3]=e[12],e[12]=t,t=e[7],e[7]=e[13],e[13]=t,t=e[11],e[11]=e[14],e[14]=t,this}setPosition(e,t,n){let i=this.elements;return e.isVector3?(i[12]=e.x,i[13]=e.y,i[14]=e.z):(i[12]=e,i[13]=t,i[14]=n),this}invert(){let e=this.elements,t=e[0],n=e[1],i=e[2],s=e[3],a=e[4],o=e[5],c=e[6],l=e[7],h=e[8],p=e[9],f=e[10],m=e[11],v=e[12],g=e[13],u=e[14],y=e[15],x=t*o-n*a,S=t*c-i*a,E=t*l-s*a,R=n*c-i*o,P=n*l-s*o,V=i*l-s*c,X=h*g-p*v,H=h*u-f*v,se=h*y-m*v,oe=p*u-f*g,te=p*y-m*g,ne=f*y-m*u,j=x*ne-S*te+E*oe+R*se-P*H+V*X;if(j===0)return this.set(0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0);let J=1/j;return e[0]=(o*ne-c*te+l*oe)*J,e[1]=(i*te-n*ne-s*oe)*J,e[2]=(g*V-u*P+y*R)*J,e[3]=(f*P-p*V-m*R)*J,e[4]=(c*se-a*ne-l*H)*J,e[5]=(t*ne-i*se+s*H)*J,e[6]=(u*E-v*V-y*S)*J,e[7]=(h*V-f*E+m*S)*J,e[8]=(a*te-o*se+l*X)*J,e[9]=(n*se-t*te-s*X)*J,e[10]=(v*P-g*E+y*x)*J,e[11]=(p*E-h*P-m*x)*J,e[12]=(o*H-a*oe-c*X)*J,e[13]=(t*oe-n*H+i*X)*J,e[14]=(g*S-v*R-u*x)*J,e[15]=(h*R-p*S+f*x)*J,this}scale(e){let t=this.elements,n=e.x,i=e.y,s=e.z;return t[0]*=n,t[4]*=i,t[8]*=s,t[1]*=n,t[5]*=i,t[9]*=s,t[2]*=n,t[6]*=i,t[10]*=s,t[3]*=n,t[7]*=i,t[11]*=s,this}getMaxScaleOnAxis(){let e=this.elements,t=e[0]*e[0]+e[1]*e[1]+e[2]*e[2],n=e[4]*e[4]+e[5]*e[5]+e[6]*e[6],i=e[8]*e[8]+e[9]*e[9]+e[10]*e[10];return Math.sqrt(Math.max(t,n,i))}makeTranslation(e,t,n){return e.isVector3?this.set(1,0,0,e.x,0,1,0,e.y,0,0,1,e.z,0,0,0,1):this.set(1,0,0,e,0,1,0,t,0,0,1,n,0,0,0,1),this}makeRotationX(e){let t=Math.cos(e),n=Math.sin(e);return this.set(1,0,0,0,0,t,-n,0,0,n,t,0,0,0,0,1),this}makeRotationY(e){let t=Math.cos(e),n=Math.sin(e);return this.set(t,0,n,0,0,1,0,0,-n,0,t,0,0,0,0,1),this}makeRotationZ(e){let t=Math.cos(e),n=Math.sin(e);return this.set(t,-n,0,0,n,t,0,0,0,0,1,0,0,0,0,1),this}makeRotationAxis(e,t){let n=Math.cos(t),i=Math.sin(t),s=1-n,a=e.x,o=e.y,c=e.z,l=s*a,h=s*o;return this.set(l*a+n,l*o-i*c,l*c+i*o,0,l*o+i*c,h*o+n,h*c-i*a,0,l*c-i*o,h*c+i*a,s*c*c+n,0,0,0,0,1),this}makeScale(e,t,n){return this.set(e,0,0,0,0,t,0,0,0,0,n,0,0,0,0,1),this}makeShear(e,t,n,i,s,a){return this.set(1,n,s,0,e,1,a,0,t,i,1,0,0,0,0,1),this}compose(e,t,n){let i=this.elements,s=t._x,a=t._y,o=t._z,c=t._w,l=s+s,h=a+a,p=o+o,f=s*l,m=s*h,v=s*p,g=a*h,u=a*p,y=o*p,x=c*l,S=c*h,E=c*p,R=n.x,P=n.y,V=n.z;return i[0]=(1-(g+y))*R,i[1]=(m+E)*R,i[2]=(v-S)*R,i[3]=0,i[4]=(m-E)*P,i[5]=(1-(f+y))*P,i[6]=(u+x)*P,i[7]=0,i[8]=(v+S)*V,i[9]=(u-x)*V,i[10]=(1-(f+g))*V,i[11]=0,i[12]=e.x,i[13]=e.y,i[14]=e.z,i[15]=1,this}decompose(e,t,n){let i=this.elements;e.x=i[12],e.y=i[13],e.z=i[14];let s=this.determinant();if(s===0)return n.set(1,1,1),t.identity(),this;let a=es.set(i[0],i[1],i[2]).length(),o=es.set(i[4],i[5],i[6]).length(),c=es.set(i[8],i[9],i[10]).length();s<0&&(a=-a),hi.copy(this);let l=1/a,h=1/o,p=1/c;return hi.elements[0]*=l,hi.elements[1]*=l,hi.elements[2]*=l,hi.elements[4]*=h,hi.elements[5]*=h,hi.elements[6]*=h,hi.elements[8]*=p,hi.elements[9]*=p,hi.elements[10]*=p,t.setFromRotationMatrix(hi),n.x=a,n.y=o,n.z=c,this}makePerspective(e,t,n,i,s,a,o=2e3,c=!1){let l=this.elements,h=2*s/(t-e),p=2*s/(n-i),f=(t+e)/(t-e),m=(n+i)/(n-i),v,g;if(c)v=s/(a-s),g=a*s/(a-s);else if(o===Xi)v=-(a+s)/(a-s),g=-2*a*s/(a-s);else{if(o!==gs)throw new Error("THREE.Matrix4.makePerspective(): Invalid coordinate system: "+o);v=-a/(a-s),g=-a*s/(a-s)}return l[0]=h,l[4]=0,l[8]=f,l[12]=0,l[1]=0,l[5]=p,l[9]=m,l[13]=0,l[2]=0,l[6]=0,l[10]=v,l[14]=g,l[3]=0,l[7]=0,l[11]=-1,l[15]=0,this}makeOrthographic(e,t,n,i,s,a,o=2e3,c=!1){let l=this.elements,h=2/(t-e),p=2/(n-i),f=-(t+e)/(t-e),m=-(n+i)/(n-i),v,g;if(c)v=1/(a-s),g=a/(a-s);else if(o===Xi)v=-2/(a-s),g=-(a+s)/(a-s);else{if(o!==gs)throw new Error("THREE.Matrix4.makeOrthographic(): Invalid coordinate system: "+o);v=-1/(a-s),g=-s/(a-s)}return l[0]=h,l[4]=0,l[8]=0,l[12]=f,l[1]=0,l[5]=p,l[9]=0,l[13]=m,l[2]=0,l[6]=0,l[10]=v,l[14]=g,l[3]=0,l[7]=0,l[11]=0,l[15]=1,this}equals(e){let t=this.elements,n=e.elements;for(let i=0;i<16;i++)if(t[i]!==n[i])return!1;return!0}fromArray(e,t=0){for(let n=0;n<16;n++)this.elements[n]=e[n+t];return this}toArray(e=[],t=0){let n=this.elements;return e[t]=n[0],e[t+1]=n[1],e[t+2]=n[2],e[t+3]=n[3],e[t+4]=n[4],e[t+5]=n[5],e[t+6]=n[6],e[t+7]=n[7],e[t+8]=n[8],e[t+9]=n[9],e[t+10]=n[10],e[t+11]=n[11],e[t+12]=n[12],e[t+13]=n[13],e[t+14]=n[14],e[t+15]=n[15],e}},es=new N,hi=new st,pm=new N(0,0,0),fm=new N(1,1,1),$i=new N,So=new N,kn=new N,Ud=new st,Nd=new Bt,Wn=class r{constructor(e=0,t=0,n=0,i=r.DEFAULT_ORDER){this.isEuler=!0,this._x=e,this._y=t,this._z=n,this._order=i}get x(){return this._x}set x(e){this._x=e,this._onChangeCallback()}get y(){return this._y}set y(e){this._y=e,this._onChangeCallback()}get z(){return this._z}set z(e){this._z=e,this._onChangeCallback()}get order(){return this._order}set order(e){this._order=e,this._onChangeCallback()}set(e,t,n,i=this._order){return this._x=e,this._y=t,this._z=n,this._order=i,this._onChangeCallback(),this}clone(){return new this.constructor(this._x,this._y,this._z,this._order)}copy(e){return this._x=e._x,this._y=e._y,this._z=e._z,this._order=e._order,this._onChangeCallback(),this}setFromRotationMatrix(e,t=this._order,n=!0){let i=e.elements,s=i[0],a=i[4],o=i[8],c=i[1],l=i[5],h=i[9],p=i[2],f=i[6],m=i[10];switch(t){case"XYZ":this._y=Math.asin(ut(o,-1,1)),Math.abs(o)<.9999999?(this._x=Math.atan2(-h,m),this._z=Math.atan2(-a,s)):(this._x=Math.atan2(f,l),this._z=0);break;case"YXZ":this._x=Math.asin(-ut(h,-1,1)),Math.abs(h)<.9999999?(this._y=Math.atan2(o,m),this._z=Math.atan2(c,l)):(this._y=Math.atan2(-p,s),this._z=0);break;case"ZXY":this._x=Math.asin(ut(f,-1,1)),Math.abs(f)<.9999999?(this._y=Math.atan2(-p,m),this._z=Math.atan2(-a,l)):(this._y=0,this._z=Math.atan2(c,s));break;case"ZYX":this._y=Math.asin(-ut(p,-1,1)),Math.abs(p)<.9999999?(this._x=Math.atan2(f,m),this._z=Math.atan2(c,s)):(this._x=0,this._z=Math.atan2(-a,l));break;case"YZX":this._z=Math.asin(ut(c,-1,1)),Math.abs(c)<.9999999?(this._x=Math.atan2(-h,l),this._y=Math.atan2(-p,s)):(this._x=0,this._y=Math.atan2(o,m));break;case"XZY":this._z=Math.asin(-ut(a,-1,1)),Math.abs(a)<.9999999?(this._x=Math.atan2(f,l),this._y=Math.atan2(o,s)):(this._x=Math.atan2(-h,m),this._y=0);break;default:tt("Euler: .setFromRotationMatrix() encountered an unknown order: "+t)}return this._order=t,n===!0&&this._onChangeCallback(),this}setFromQuaternion(e,t,n){return Ud.makeRotationFromQuaternion(e),this.setFromRotationMatrix(Ud,t,n)}setFromVector3(e,t=this._order){return this.set(e.x,e.y,e.z,t)}reorder(e){return Nd.setFromEuler(this),this.setFromQuaternion(Nd,e)}equals(e){return e._x===this._x&&e._y===this._y&&e._z===this._z&&e._order===this._order}fromArray(e){return this._x=e[0],this._y=e[1],this._z=e[2],e[3]!==void 0&&(this._order=e[3]),this._onChangeCallback(),this}toArray(e=[],t=0){return e[t]=this._x,e[t+1]=this._y,e[t+2]=this._z,e[t+3]=this._order,e}_onChange(e){return this._onChangeCallback=e,this}_onChangeCallback(){}*[Symbol.iterator](){yield this._x,yield this._y,yield this._z,yield this._order}};Wn.DEFAULT_ORDER="XYZ";var Ms=class{constructor(){this.mask=1}set(e){this.mask=1<<e>>>0}enable(e){this.mask|=1<<e}enableAll(){this.mask=-1}toggle(e){this.mask^=1<<e}disable(e){this.mask&=~(1<<e)}disableAll(){this.mask=0}test(e){return(this.mask&e.mask)!==0}isEnabled(e){return!!(this.mask&1<<e)}},mm=0,Od=new N,ts=new Bt,zi=new st,To=new N,aa=new N,gm=new N,_m=new Bt,Fd=new N(1,0,0),Bd=new N(0,1,0),zd=new N(0,0,1),kd={type:"added"},vm={type:"removed"},ns={type:"childadded",child:null},Yc={type:"childremoved",child:null},Gt=class r extends bi{constructor(){super(),this.isObject3D=!0,Object.defineProperty(this,"id",{value:mm++}),this.uuid=Br(),this.name="",this.type="Object3D",this.parent=null,this.children=[],this.up=r.DEFAULT_UP.clone();let e=new N,t=new Wn,n=new Bt,i=new N(1,1,1);t._onChange(function(){n.setFromEuler(t,!1)}),n._onChange(function(){t.setFromQuaternion(n,void 0,!1)}),Object.defineProperties(this,{position:{configurable:!0,enumerable:!0,value:e},rotation:{configurable:!0,enumerable:!0,value:t},quaternion:{configurable:!0,enumerable:!0,value:n},scale:{configurable:!0,enumerable:!0,value:i},modelViewMatrix:{value:new st},normalMatrix:{value:new ht}}),this.matrix=new st,this.matrixWorld=new st,this.matrixAutoUpdate=r.DEFAULT_MATRIX_AUTO_UPDATE,this.matrixWorldAutoUpdate=r.DEFAULT_MATRIX_WORLD_AUTO_UPDATE,this.matrixWorldNeedsUpdate=!1,this.layers=new Ms,this.visible=!0,this.castShadow=!1,this.receiveShadow=!1,this.frustumCulled=!0,this.renderOrder=0,this.animations=[],this.customDepthMaterial=void 0,this.customDistanceMaterial=void 0,this.static=!1,this.userData={},this.pivot=null}onBeforeShadow(){}onAfterShadow(){}onBeforeRender(){}onAfterRender(){}applyMatrix4(e){this.matrixAutoUpdate&&this.updateMatrix(),this.matrix.premultiply(e),this.matrix.decompose(this.position,this.quaternion,this.scale)}applyQuaternion(e){return this.quaternion.premultiply(e),this}setRotationFromAxisAngle(e,t){this.quaternion.setFromAxisAngle(e,t)}setRotationFromEuler(e){this.quaternion.setFromEuler(e,!0)}setRotationFromMatrix(e){this.quaternion.setFromRotationMatrix(e)}setRotationFromQuaternion(e){this.quaternion.copy(e)}rotateOnAxis(e,t){return ts.setFromAxisAngle(e,t),this.quaternion.multiply(ts),this}rotateOnWorldAxis(e,t){return ts.setFromAxisAngle(e,t),this.quaternion.premultiply(ts),this}rotateX(e){return this.rotateOnAxis(Fd,e)}rotateY(e){return this.rotateOnAxis(Bd,e)}rotateZ(e){return this.rotateOnAxis(zd,e)}translateOnAxis(e,t){return Od.copy(e).applyQuaternion(this.quaternion),this.position.add(Od.multiplyScalar(t)),this}translateX(e){return this.translateOnAxis(Fd,e)}translateY(e){return this.translateOnAxis(Bd,e)}translateZ(e){return this.translateOnAxis(zd,e)}localToWorld(e){return this.updateWorldMatrix(!0,!1),e.applyMatrix4(this.matrixWorld)}worldToLocal(e){return this.updateWorldMatrix(!0,!1),e.applyMatrix4(zi.copy(this.matrixWorld).invert())}lookAt(e,t,n){e.isVector3?To.copy(e):To.set(e,t,n);let i=this.parent;this.updateWorldMatrix(!0,!1),aa.setFromMatrixPosition(this.matrixWorld),this.isCamera||this.isLight?zi.lookAt(aa,To,this.up):zi.lookAt(To,aa,this.up),this.quaternion.setFromRotationMatrix(zi),i&&(zi.extractRotation(i.matrixWorld),ts.setFromRotationMatrix(zi),this.quaternion.premultiply(ts.invert()))}add(e){if(arguments.length>1){for(let t=0;t<arguments.length;t++)this.add(arguments[t]);return this}return e===this?(rt("Object3D.add: object can't be added as a child of itself.",e),this):(e&&e.isObject3D?(e.removeFromParent(),e.parent=this,this.children.push(e),e.dispatchEvent(kd),ns.child=e,this.dispatchEvent(ns),ns.child=null):rt("Object3D.add: object not an instance of THREE.Object3D.",e),this)}remove(e){if(arguments.length>1){for(let n=0;n<arguments.length;n++)this.remove(arguments[n]);return this}let t=this.children.indexOf(e);return t!==-1&&(e.parent=null,this.children.splice(t,1),e.dispatchEvent(vm),Yc.child=e,this.dispatchEvent(Yc),Yc.child=null),this}removeFromParent(){let e=this.parent;return e!==null&&e.remove(this),this}clear(){return this.remove(...this.children)}attach(e){return this.updateWorldMatrix(!0,!1),zi.copy(this.matrixWorld).invert(),e.parent!==null&&(e.parent.updateWorldMatrix(!0,!1),zi.multiply(e.parent.matrixWorld)),e.applyMatrix4(zi),e.removeFromParent(),e.parent=this,this.children.push(e),e.updateWorldMatrix(!1,!0),e.dispatchEvent(kd),ns.child=e,this.dispatchEvent(ns),ns.child=null,this}getObjectById(e){return this.getObjectByProperty("id",e)}getObjectByName(e){return this.getObjectByProperty("name",e)}getObjectByProperty(e,t){if(this[e]===t)return this;for(let n=0,i=this.children.length;n<i;n++){let s=this.children[n].getObjectByProperty(e,t);if(s!==void 0)return s}}getObjectsByProperty(e,t,n=[]){this[e]===t&&n.push(this);let i=this.children;for(let s=0,a=i.length;s<a;s++)i[s].getObjectsByProperty(e,t,n);return n}getWorldPosition(e){return this.updateWorldMatrix(!0,!1),e.setFromMatrixPosition(this.matrixWorld)}getWorldQuaternion(e){return this.updateWorldMatrix(!0,!1),this.matrixWorld.decompose(aa,e,gm),e}getWorldScale(e){return this.updateWorldMatrix(!0,!1),this.matrixWorld.decompose(aa,_m,e),e}getWorldDirection(e){this.updateWorldMatrix(!0,!1);let t=this.matrixWorld.elements;return e.set(t[8],t[9],t[10]).normalize()}raycast(){}traverse(e){e(this);let t=this.children;for(let n=0,i=t.length;n<i;n++)t[n].traverse(e)}traverseVisible(e){if(this.visible===!1)return;e(this);let t=this.children;for(let n=0,i=t.length;n<i;n++)t[n].traverseVisible(e)}traverseAncestors(e){let t=this.parent;t!==null&&(e(t),t.traverseAncestors(e))}updateMatrix(){this.matrix.compose(this.position,this.quaternion,this.scale);let e=this.pivot;if(e!==null){let t=e.x,n=e.y,i=e.z,s=this.matrix.elements;s[12]+=t-s[0]*t-s[4]*n-s[8]*i,s[13]+=n-s[1]*t-s[5]*n-s[9]*i,s[14]+=i-s[2]*t-s[6]*n-s[10]*i}this.matrixWorldNeedsUpdate=!0}updateMatrixWorld(e){this.matrixAutoUpdate&&this.updateMatrix(),(this.matrixWorldNeedsUpdate||e)&&(this.matrixWorldAutoUpdate===!0&&(this.parent===null?this.matrixWorld.copy(this.matrix):this.matrixWorld.multiplyMatrices(this.parent.matrixWorld,this.matrix)),this.matrixWorldNeedsUpdate=!1,e=!0);let t=this.children;for(let n=0,i=t.length;n<i;n++)t[n].updateMatrixWorld(e)}updateWorldMatrix(e,t){let n=this.parent;if(e===!0&&n!==null&&n.updateWorldMatrix(!0,!1),this.matrixAutoUpdate&&this.updateMatrix(),this.matrixWorldAutoUpdate===!0&&(this.parent===null?this.matrixWorld.copy(this.matrix):this.matrixWorld.multiplyMatrices(this.parent.matrixWorld,this.matrix)),t===!0){let i=this.children;for(let s=0,a=i.length;s<a;s++)i[s].updateWorldMatrix(!1,!0)}}toJSON(e){let t=e===void 0||typeof e=="string",n={};t&&(e={geometries:{},materials:{},textures:{},images:{},shapes:{},skeletons:{},animations:{},nodes:{}},n.metadata={version:4.7,type:"Object",generator:"Object3D.toJSON"});let i={};function s(o,c){return o[c.uuid]===void 0&&(o[c.uuid]=c.toJSON(e)),c.uuid}if(i.uuid=this.uuid,i.type=this.type,this.name!==""&&(i.name=this.name),this.castShadow===!0&&(i.castShadow=!0),this.receiveShadow===!0&&(i.receiveShadow=!0),this.visible===!1&&(i.visible=!1),this.frustumCulled===!1&&(i.frustumCulled=!1),this.renderOrder!==0&&(i.renderOrder=this.renderOrder),this.static!==!1&&(i.static=this.static),Object.keys(this.userData).length>0&&(i.userData=this.userData),i.layers=this.layers.mask,i.matrix=this.matrix.toArray(),i.up=this.up.toArray(),this.pivot!==null&&(i.pivot=this.pivot.toArray()),this.matrixAutoUpdate===!1&&(i.matrixAutoUpdate=!1),this.morphTargetDictionary!==void 0&&(i.morphTargetDictionary=Object.assign({},this.morphTargetDictionary)),this.morphTargetInfluences!==void 0&&(i.morphTargetInfluences=this.morphTargetInfluences.slice()),this.isInstancedMesh&&(i.type="InstancedMesh",i.count=this.count,i.instanceMatrix=this.instanceMatrix.toJSON(),this.instanceColor!==null&&(i.instanceColor=this.instanceColor.toJSON())),this.isBatchedMesh&&(i.type="BatchedMesh",i.perObjectFrustumCulled=this.perObjectFrustumCulled,i.sortObjects=this.sortObjects,i.drawRanges=this._drawRanges,i.reservedRanges=this._reservedRanges,i.geometryInfo=this._geometryInfo.map(o=>({...o,boundingBox:o.boundingBox?o.boundingBox.toJSON():void 0,boundingSphere:o.boundingSphere?o.boundingSphere.toJSON():void 0})),i.instanceInfo=this._instanceInfo.map(o=>({...o})),i.availableInstanceIds=this._availableInstanceIds.slice(),i.availableGeometryIds=this._availableGeometryIds.slice(),i.nextIndexStart=this._nextIndexStart,i.nextVertexStart=this._nextVertexStart,i.geometryCount=this._geometryCount,i.maxInstanceCount=this._maxInstanceCount,i.maxVertexCount=this._maxVertexCount,i.maxIndexCount=this._maxIndexCount,i.geometryInitialized=this._geometryInitialized,i.matricesTexture=this._matricesTexture.toJSON(e),i.indirectTexture=this._indirectTexture.toJSON(e),this._colorsTexture!==null&&(i.colorsTexture=this._colorsTexture.toJSON(e)),this.boundingSphere!==null&&(i.boundingSphere=this.boundingSphere.toJSON()),this.boundingBox!==null&&(i.boundingBox=this.boundingBox.toJSON())),this.isScene)this.background&&(this.background.isColor?i.background=this.background.toJSON():this.background.isTexture&&(i.background=this.background.toJSON(e).uuid)),this.environment&&this.environment.isTexture&&this.environment.isRenderTargetTexture!==!0&&(i.environment=this.environment.toJSON(e).uuid);else if(this.isMesh||this.isLine||this.isPoints){i.geometry=s(e.geometries,this.geometry);let o=this.geometry.parameters;if(o!==void 0&&o.shapes!==void 0){let c=o.shapes;if(Array.isArray(c))for(let l=0,h=c.length;l<h;l++){let p=c[l];s(e.shapes,p)}else s(e.shapes,c)}}if(this.isSkinnedMesh&&(i.bindMode=this.bindMode,i.bindMatrix=this.bindMatrix.toArray(),this.skeleton!==void 0&&(s(e.skeletons,this.skeleton),i.skeleton=this.skeleton.uuid)),this.material!==void 0)if(Array.isArray(this.material)){let o=[];for(let c=0,l=this.material.length;c<l;c++)o.push(s(e.materials,this.material[c]));i.material=o}else i.material=s(e.materials,this.material);if(this.children.length>0){i.children=[];for(let o=0;o<this.children.length;o++)i.children.push(this.children[o].toJSON(e).object)}if(this.animations.length>0){i.animations=[];for(let o=0;o<this.animations.length;o++){let c=this.animations[o];i.animations.push(s(e.animations,c))}}if(t){let o=a(e.geometries),c=a(e.materials),l=a(e.textures),h=a(e.images),p=a(e.shapes),f=a(e.skeletons),m=a(e.animations),v=a(e.nodes);o.length>0&&(n.geometries=o),c.length>0&&(n.materials=c),l.length>0&&(n.textures=l),h.length>0&&(n.images=h),p.length>0&&(n.shapes=p),f.length>0&&(n.skeletons=f),m.length>0&&(n.animations=m),v.length>0&&(n.nodes=v)}return n.object=i,n;function a(o){let c=[];for(let l in o){let h=o[l];delete h.metadata,c.push(h)}return c}}clone(e){return new this.constructor().copy(this,e)}copy(e,t=!0){if(this.name=e.name,this.up.copy(e.up),this.position.copy(e.position),this.rotation.order=e.rotation.order,this.quaternion.copy(e.quaternion),this.scale.copy(e.scale),e.pivot!==null&&(this.pivot=e.pivot.clone()),this.matrix.copy(e.matrix),this.matrixWorld.copy(e.matrixWorld),this.matrixAutoUpdate=e.matrixAutoUpdate,this.matrixWorldAutoUpdate=e.matrixWorldAutoUpdate,this.matrixWorldNeedsUpdate=e.matrixWorldNeedsUpdate,this.layers.mask=e.layers.mask,this.visible=e.visible,this.castShadow=e.castShadow,this.receiveShadow=e.receiveShadow,this.frustumCulled=e.frustumCulled,this.renderOrder=e.renderOrder,this.static=e.static,this.animations=e.animations.slice(),this.userData=JSON.parse(JSON.stringify(e.userData)),t===!0)for(let n=0;n<e.children.length;n++){let i=e.children[n];this.add(i.clone())}return this}};Gt.DEFAULT_UP=new N(0,1,0),Gt.DEFAULT_MATRIX_AUTO_UPDATE=!0,Gt.DEFAULT_MATRIX_WORLD_AUTO_UPDATE=!0;var Kt=class extends Gt{constructor(){super(),this.isGroup=!0,this.type="Group"}},ym={type:"move"},bs=class{constructor(){this._targetRay=null,this._grip=null,this._hand=null}getHandSpace(){return this._hand===null&&(this._hand=new Kt,this._hand.matrixAutoUpdate=!1,this._hand.visible=!1,this._hand.joints={},this._hand.inputState={pinching:!1}),this._hand}getTargetRaySpace(){return this._targetRay===null&&(this._targetRay=new Kt,this._targetRay.matrixAutoUpdate=!1,this._targetRay.visible=!1,this._targetRay.hasLinearVelocity=!1,this._targetRay.linearVelocity=new N,this._targetRay.hasAngularVelocity=!1,this._targetRay.angularVelocity=new N),this._targetRay}getGripSpace(){return this._grip===null&&(this._grip=new Kt,this._grip.matrixAutoUpdate=!1,this._grip.visible=!1,this._grip.hasLinearVelocity=!1,this._grip.linearVelocity=new N,this._grip.hasAngularVelocity=!1,this._grip.angularVelocity=new N),this._grip}dispatchEvent(e){return this._targetRay!==null&&this._targetRay.dispatchEvent(e),this._grip!==null&&this._grip.dispatchEvent(e),this._hand!==null&&this._hand.dispatchEvent(e),this}connect(e){if(e&&e.hand){let t=this._hand;if(t)for(let n of e.hand.values())this._getHandJoint(t,n)}return this.dispatchEvent({type:"connected",data:e}),this}disconnect(e){return this.dispatchEvent({type:"disconnected",data:e}),this._targetRay!==null&&(this._targetRay.visible=!1),this._grip!==null&&(this._grip.visible=!1),this._hand!==null&&(this._hand.visible=!1),this}update(e,t,n){let i=null,s=null,a=null,o=this._targetRay,c=this._grip,l=this._hand;if(e&&t.session.visibilityState!=="visible-blurred"){if(l&&e.hand){a=!0;for(let g of e.hand.values()){let u=t.getJointPose(g,n),y=this._getHandJoint(l,g);u!==null&&(y.matrix.fromArray(u.transform.matrix),y.matrix.decompose(y.position,y.rotation,y.scale),y.matrixWorldNeedsUpdate=!0,y.jointRadius=u.radius),y.visible=u!==null}let h=l.joints["index-finger-tip"],p=l.joints["thumb-tip"],f=h.position.distanceTo(p.position),m=.02,v=.005;l.inputState.pinching&&f>m+v?(l.inputState.pinching=!1,this.dispatchEvent({type:"pinchend",handedness:e.handedness,target:this})):!l.inputState.pinching&&f<=m-v&&(l.inputState.pinching=!0,this.dispatchEvent({type:"pinchstart",handedness:e.handedness,target:this}))}else c!==null&&e.gripSpace&&(s=t.getPose(e.gripSpace,n),s!==null&&(c.matrix.fromArray(s.transform.matrix),c.matrix.decompose(c.position,c.rotation,c.scale),c.matrixWorldNeedsUpdate=!0,s.linearVelocity?(c.hasLinearVelocity=!0,c.linearVelocity.copy(s.linearVelocity)):c.hasLinearVelocity=!1,s.angularVelocity?(c.hasAngularVelocity=!0,c.angularVelocity.copy(s.angularVelocity)):c.hasAngularVelocity=!1));o!==null&&(i=t.getPose(e.targetRaySpace,n),i===null&&s!==null&&(i=s),i!==null&&(o.matrix.fromArray(i.transform.matrix),o.matrix.decompose(o.position,o.rotation,o.scale),o.matrixWorldNeedsUpdate=!0,i.linearVelocity?(o.hasLinearVelocity=!0,o.linearVelocity.copy(i.linearVelocity)):o.hasLinearVelocity=!1,i.angularVelocity?(o.hasAngularVelocity=!0,o.angularVelocity.copy(i.angularVelocity)):o.hasAngularVelocity=!1,this.dispatchEvent(ym)))}return o!==null&&(o.visible=i!==null),c!==null&&(c.visible=s!==null),l!==null&&(l.visible=a!==null),this}_getHandJoint(e,t){if(e.joints[t.jointName]===void 0){let n=new Kt;n.matrixAutoUpdate=!1,n.visible=!1,e.joints[t.jointName]=n,e.add(n)}return e.joints[t.jointName]}},Qp={aliceblue:15792383,antiquewhite:16444375,aqua:65535,aquamarine:8388564,azure:15794175,beige:16119260,bisque:16770244,black:0,blanchedalmond:16772045,blue:255,blueviolet:9055202,brown:10824234,burlywood:14596231,cadetblue:6266528,chartreuse:8388352,chocolate:13789470,coral:16744272,cornflowerblue:6591981,cornsilk:16775388,crimson:14423100,cyan:65535,darkblue:139,darkcyan:35723,darkgoldenrod:12092939,darkgray:11119017,darkgreen:25600,darkgrey:11119017,darkkhaki:12433259,darkmagenta:9109643,darkolivegreen:5597999,darkorange:16747520,darkorchid:10040012,darkred:9109504,darksalmon:15308410,darkseagreen:9419919,darkslateblue:4734347,darkslategray:3100495,darkslategrey:3100495,darkturquoise:52945,darkviolet:9699539,deeppink:16716947,deepskyblue:49151,dimgray:6908265,dimgrey:6908265,dodgerblue:2003199,firebrick:11674146,floralwhite:16775920,forestgreen:2263842,fuchsia:16711935,gainsboro:14474460,ghostwhite:16316671,gold:16766720,goldenrod:14329120,gray:8421504,green:32768,greenyellow:11403055,grey:8421504,honeydew:15794160,hotpink:16738740,indianred:13458524,indigo:4915330,ivory:16777200,khaki:15787660,lavender:15132410,lavenderblush:16773365,lawngreen:8190976,lemonchiffon:16775885,lightblue:11393254,lightcoral:15761536,lightcyan:14745599,lightgoldenrodyellow:16448210,lightgray:13882323,lightgreen:9498256,lightgrey:13882323,lightpink:16758465,lightsalmon:16752762,lightseagreen:2142890,lightskyblue:8900346,lightslategray:7833753,lightslategrey:7833753,lightsteelblue:11584734,lightyellow:16777184,lime:65280,limegreen:3329330,linen:16445670,magenta:16711935,maroon:8388608,mediumaquamarine:6737322,mediumblue:205,mediumorchid:12211667,mediumpurple:9662683,mediumseagreen:3978097,mediumslateblue:8087790,mediumspringgreen:64154,mediumturquoise:4772300,mediumvioletred:13047173,midnightblue:1644912,mintcream:16121850,mistyrose:16770273,moccasin:16770229,navajowhite:16768685,navy:128,oldlace:16643558,olive:8421376,olivedrab:7048739,orange:16753920,orangered:16729344,orchid:14315734,palegoldenrod:15657130,palegreen:10025880,paleturquoise:11529966,palevioletred:14381203,papayawhip:16773077,peachpuff:16767673,peru:13468991,pink:16761035,plum:14524637,powderblue:11591910,purple:8388736,rebeccapurple:6697881,red:16711680,rosybrown:12357519,royalblue:4286945,saddlebrown:9127187,salmon:16416882,sandybrown:16032864,seagreen:3050327,seashell:16774638,sienna:10506797,silver:12632256,skyblue:8900331,slateblue:6970061,slategray:7372944,slategrey:7372944,snow:16775930,springgreen:65407,steelblue:4620980,tan:13808780,teal:32896,thistle:14204888,tomato:16737095,turquoise:4251856,violet:15631086,wheat:16113331,white:16777215,whitesmoke:16119285,yellow:16776960,yellowgreen:10145074},Qi={h:0,s:0,l:0},Eo={h:0,s:0,l:0};function Zc(r,e,t){return t<0&&(t+=1),t>1&&(t-=1),t<1/6?r+6*(e-r)*t:t<.5?e:t<2/3?r+6*(e-r)*(2/3-t):r}var ke=class{constructor(e,t,n){return this.isColor=!0,this.r=1,this.g=1,this.b=1,this.set(e,t,n)}set(e,t,n){if(t===void 0&&n===void 0){let i=e;i&&i.isColor?this.copy(i):typeof i=="number"?this.setHex(i):typeof i=="string"&&this.setStyle(i)}else this.setRGB(e,t,n);return this}setScalar(e){return this.r=e,this.g=e,this.b=e,this}setHex(e,t=Xt){return e=Math.floor(e),this.r=(e>>16&255)/255,this.g=(e>>8&255)/255,this.b=(255&e)/255,Mt.colorSpaceToWorking(this,t),this}setRGB(e,t,n,i=Mt.workingColorSpace){return this.r=e,this.g=t,this.b=n,Mt.colorSpaceToWorking(this,i),this}setHSL(e,t,n,i=Mt.workingColorSpace){if(e=Sh(e,1),t=ut(t,0,1),n=ut(n,0,1),t===0)this.r=this.g=this.b=n;else{let s=n<=.5?n*(1+t):n+t-n*t,a=2*n-s;this.r=Zc(a,s,e+1/3),this.g=Zc(a,s,e),this.b=Zc(a,s,e-1/3)}return Mt.colorSpaceToWorking(this,i),this}setStyle(e,t=Xt){function n(s){s!==void 0&&parseFloat(s)<1&&tt("Color: Alpha component of "+e+" will be ignored.")}let i;if(i=/^(\w+)\(([^\)]*)\)/.exec(e)){let s,a=i[1],o=i[2];switch(a){case"rgb":case"rgba":if(s=/^\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(o))return n(s[4]),this.setRGB(Math.min(255,parseInt(s[1],10))/255,Math.min(255,parseInt(s[2],10))/255,Math.min(255,parseInt(s[3],10))/255,t);if(s=/^\s*(\d+)\%\s*,\s*(\d+)\%\s*,\s*(\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(o))return n(s[4]),this.setRGB(Math.min(100,parseInt(s[1],10))/100,Math.min(100,parseInt(s[2],10))/100,Math.min(100,parseInt(s[3],10))/100,t);break;case"hsl":case"hsla":if(s=/^\s*(\d*\.?\d+)\s*,\s*(\d*\.?\d+)\%\s*,\s*(\d*\.?\d+)\%\s*(?:,\s*(\d*\.?\d+)\s*)?$/.exec(o))return n(s[4]),this.setHSL(parseFloat(s[1])/360,parseFloat(s[2])/100,parseFloat(s[3])/100,t);break;default:tt("Color: Unknown color model "+e)}}else if(i=/^\#([A-Fa-f\d]+)$/.exec(e)){let s=i[1],a=s.length;if(a===3)return this.setRGB(parseInt(s.charAt(0),16)/15,parseInt(s.charAt(1),16)/15,parseInt(s.charAt(2),16)/15,t);if(a===6)return this.setHex(parseInt(s,16),t);tt("Color: Invalid hex color "+e)}else if(e&&e.length>0)return this.setColorName(e,t);return this}setColorName(e,t=Xt){let n=Qp[e.toLowerCase()];return n!==void 0?this.setHex(n,t):tt("Color: Unknown color "+e),this}clone(){return new this.constructor(this.r,this.g,this.b)}copy(e){return this.r=e.r,this.g=e.g,this.b=e.b,this}copySRGBToLinear(e){return this.r=ji(e.r),this.g=ji(e.g),this.b=ji(e.b),this}copyLinearToSRGB(e){return this.r=fs(e.r),this.g=fs(e.g),this.b=fs(e.b),this}convertSRGBToLinear(){return this.copySRGBToLinear(this),this}convertLinearToSRGB(){return this.copyLinearToSRGB(this),this}getHex(e=Xt){return Mt.workingToColorSpace(gn.copy(this),e),65536*Math.round(ut(255*gn.r,0,255))+256*Math.round(ut(255*gn.g,0,255))+Math.round(ut(255*gn.b,0,255))}getHexString(e=Xt){return("000000"+this.getHex(e).toString(16)).slice(-6)}getHSL(e,t=Mt.workingColorSpace){Mt.workingToColorSpace(gn.copy(this),t);let n=gn.r,i=gn.g,s=gn.b,a=Math.max(n,i,s),o=Math.min(n,i,s),c,l,h=(o+a)/2;if(o===a)c=0,l=0;else{let p=a-o;switch(l=h<=.5?p/(a+o):p/(2-a-o),a){case n:c=(i-s)/p+(i<s?6:0);break;case i:c=(s-n)/p+2;break;case s:c=(n-i)/p+4}c/=6}return e.h=c,e.s=l,e.l=h,e}getRGB(e,t=Mt.workingColorSpace){return Mt.workingToColorSpace(gn.copy(this),t),e.r=gn.r,e.g=gn.g,e.b=gn.b,e}getStyle(e=Xt){Mt.workingToColorSpace(gn.copy(this),e);let t=gn.r,n=gn.g,i=gn.b;return e!==Xt?`color(${e} ${t.toFixed(3)} ${n.toFixed(3)} ${i.toFixed(3)})`:`rgb(${Math.round(255*t)},${Math.round(255*n)},${Math.round(255*i)})`}offsetHSL(e,t,n){return this.getHSL(Qi),this.setHSL(Qi.h+e,Qi.s+t,Qi.l+n)}add(e){return this.r+=e.r,this.g+=e.g,this.b+=e.b,this}addColors(e,t){return this.r=e.r+t.r,this.g=e.g+t.g,this.b=e.b+t.b,this}addScalar(e){return this.r+=e,this.g+=e,this.b+=e,this}sub(e){return this.r=Math.max(0,this.r-e.r),this.g=Math.max(0,this.g-e.g),this.b=Math.max(0,this.b-e.b),this}multiply(e){return this.r*=e.r,this.g*=e.g,this.b*=e.b,this}multiplyScalar(e){return this.r*=e,this.g*=e,this.b*=e,this}lerp(e,t){return this.r+=(e.r-this.r)*t,this.g+=(e.g-this.g)*t,this.b+=(e.b-this.b)*t,this}lerpColors(e,t,n){return this.r=e.r+(t.r-e.r)*n,this.g=e.g+(t.g-e.g)*n,this.b=e.b+(t.b-e.b)*n,this}lerpHSL(e,t){this.getHSL(Qi),e.getHSL(Eo);let n=fa(Qi.h,Eo.h,t),i=fa(Qi.s,Eo.s,t),s=fa(Qi.l,Eo.l,t);return this.setHSL(n,i,s),this}setFromVector3(e){return this.r=e.x,this.g=e.y,this.b=e.z,this}applyMatrix3(e){let t=this.r,n=this.g,i=this.b,s=e.elements;return this.r=s[0]*t+s[3]*n+s[6]*i,this.g=s[1]*t+s[4]*n+s[7]*i,this.b=s[2]*t+s[5]*n+s[8]*i,this}equals(e){return e.r===this.r&&e.g===this.g&&e.b===this.b}fromArray(e,t=0){return this.r=e[t],this.g=e[t+1],this.b=e[t+2],this}toArray(e=[],t=0){return e[t]=this.r,e[t+1]=this.g,e[t+2]=this.b,e}fromBufferAttribute(e,t){return this.r=e.getX(t),this.g=e.getY(t),this.b=e.getZ(t),this}toJSON(){return this.getHex()}*[Symbol.iterator](){yield this.r,yield this.g,yield this.b}},gn=new ke;ke.NAMES=Qp;var Ma=class r{constructor(e,t=1,n=1e3){this.isFog=!0,this.name="",this.color=new ke(e),this.near=t,this.far=n}clone(){return new r(this.color,this.near,this.far)}toJSON(){return{type:"Fog",name:this.name,color:this.color.getHex(),near:this.near,far:this.far}}},ba=class extends Gt{constructor(){super(),this.isScene=!0,this.type="Scene",this.background=null,this.environment=null,this.fog=null,this.backgroundBlurriness=0,this.backgroundIntensity=1,this.backgroundRotation=new Wn,this.environmentIntensity=1,this.environmentRotation=new Wn,this.overrideMaterial=null,typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("observe",{detail:this}))}copy(e,t){return super.copy(e,t),e.background!==null&&(this.background=e.background.clone()),e.environment!==null&&(this.environment=e.environment.clone()),e.fog!==null&&(this.fog=e.fog.clone()),this.backgroundBlurriness=e.backgroundBlurriness,this.backgroundIntensity=e.backgroundIntensity,this.backgroundRotation.copy(e.backgroundRotation),this.environmentIntensity=e.environmentIntensity,this.environmentRotation.copy(e.environmentRotation),e.overrideMaterial!==null&&(this.overrideMaterial=e.overrideMaterial.clone()),this.matrixAutoUpdate=e.matrixAutoUpdate,this}toJSON(e){let t=super.toJSON(e);return this.fog!==null&&(t.object.fog=this.fog.toJSON()),this.backgroundBlurriness>0&&(t.object.backgroundBlurriness=this.backgroundBlurriness),this.backgroundIntensity!==1&&(t.object.backgroundIntensity=this.backgroundIntensity),t.object.backgroundRotation=this.backgroundRotation.toArray(),this.environmentIntensity!==1&&(t.object.environmentIntensity=this.environmentIntensity),t.object.environmentRotation=this.environmentRotation.toArray(),t}},ui=new N,ki=new N,Jc=new N,Hi=new N,is=new N,rs=new N,Hd=new N,Kc=new N,$c=new N,Qc=new N,eh=new Lt,th=new Lt,nh=new Lt,Wi=class r{constructor(e=new N,t=new N,n=new N){this.a=e,this.b=t,this.c=n}static getNormal(e,t,n,i){i.subVectors(n,t),ui.subVectors(e,t),i.cross(ui);let s=i.lengthSq();return s>0?i.multiplyScalar(1/Math.sqrt(s)):i.set(0,0,0)}static getBarycoord(e,t,n,i,s){ui.subVectors(i,t),ki.subVectors(n,t),Jc.subVectors(e,t);let a=ui.dot(ui),o=ui.dot(ki),c=ui.dot(Jc),l=ki.dot(ki),h=ki.dot(Jc),p=a*l-o*o;if(p===0)return s.set(0,0,0),null;let f=1/p,m=(l*c-o*h)*f,v=(a*h-o*c)*f;return s.set(1-m-v,v,m)}static containsPoint(e,t,n,i){return this.getBarycoord(e,t,n,i,Hi)!==null&&Hi.x>=0&&Hi.y>=0&&Hi.x+Hi.y<=1}static getInterpolation(e,t,n,i,s,a,o,c){return this.getBarycoord(e,t,n,i,Hi)===null?(c.x=0,c.y=0,"z"in c&&(c.z=0),"w"in c&&(c.w=0),null):(c.setScalar(0),c.addScaledVector(s,Hi.x),c.addScaledVector(a,Hi.y),c.addScaledVector(o,Hi.z),c)}static getInterpolatedAttribute(e,t,n,i,s,a){return eh.setScalar(0),th.setScalar(0),nh.setScalar(0),eh.fromBufferAttribute(e,t),th.fromBufferAttribute(e,n),nh.fromBufferAttribute(e,i),a.setScalar(0),a.addScaledVector(eh,s.x),a.addScaledVector(th,s.y),a.addScaledVector(nh,s.z),a}static isFrontFacing(e,t,n,i){return ui.subVectors(n,t),ki.subVectors(e,t),ui.cross(ki).dot(i)<0}set(e,t,n){return this.a.copy(e),this.b.copy(t),this.c.copy(n),this}setFromPointsAndIndices(e,t,n,i){return this.a.copy(e[t]),this.b.copy(e[n]),this.c.copy(e[i]),this}setFromAttributeAndIndices(e,t,n,i){return this.a.fromBufferAttribute(e,t),this.b.fromBufferAttribute(e,n),this.c.fromBufferAttribute(e,i),this}clone(){return new this.constructor().copy(this)}copy(e){return this.a.copy(e.a),this.b.copy(e.b),this.c.copy(e.c),this}getArea(){return ui.subVectors(this.c,this.b),ki.subVectors(this.a,this.b),.5*ui.cross(ki).length()}getMidpoint(e){return e.addVectors(this.a,this.b).add(this.c).multiplyScalar(1/3)}getNormal(e){return r.getNormal(this.a,this.b,this.c,e)}getPlane(e){return e.setFromCoplanarPoints(this.a,this.b,this.c)}getBarycoord(e,t){return r.getBarycoord(e,this.a,this.b,this.c,t)}getInterpolation(e,t,n,i,s){return r.getInterpolation(e,this.a,this.b,this.c,t,n,i,s)}containsPoint(e){return r.containsPoint(e,this.a,this.b,this.c)}isFrontFacing(e){return r.isFrontFacing(this.a,this.b,this.c,e)}intersectsBox(e){return e.intersectsTriangle(this)}closestPointToPoint(e,t){let n=this.a,i=this.b,s=this.c,a,o;is.subVectors(i,n),rs.subVectors(s,n),Kc.subVectors(e,n);let c=is.dot(Kc),l=rs.dot(Kc);if(c<=0&&l<=0)return t.copy(n);$c.subVectors(e,i);let h=is.dot($c),p=rs.dot($c);if(h>=0&&p<=h)return t.copy(i);let f=c*p-h*l;if(f<=0&&c>=0&&h<=0)return a=c/(c-h),t.copy(n).addScaledVector(is,a);Qc.subVectors(e,s);let m=is.dot(Qc),v=rs.dot(Qc);if(v>=0&&m<=v)return t.copy(s);let g=m*l-c*v;if(g<=0&&l>=0&&v<=0)return o=l/(l-v),t.copy(n).addScaledVector(rs,o);let u=h*v-m*p;if(u<=0&&p-h>=0&&m-v>=0)return Hd.subVectors(s,i),o=(p-h)/(p-h+(m-v)),t.copy(i).addScaledVector(Hd,o);let y=1/(u+g+f);return a=g*y,o=f*y,t.copy(n).addScaledVector(is,a).addScaledVector(rs,o)}equals(e){return e.a.equals(this.a)&&e.b.equals(this.b)&&e.c.equals(this.c)}},ln=class{constructor(e=new N(1/0,1/0,1/0),t=new N(-1/0,-1/0,-1/0)){this.isBox3=!0,this.min=e,this.max=t}set(e,t){return this.min.copy(e),this.max.copy(t),this}setFromArray(e){this.makeEmpty();for(let t=0,n=e.length;t<n;t+=3)this.expandByPoint(di.fromArray(e,t));return this}setFromBufferAttribute(e){this.makeEmpty();for(let t=0,n=e.count;t<n;t++)this.expandByPoint(di.fromBufferAttribute(e,t));return this}setFromPoints(e){this.makeEmpty();for(let t=0,n=e.length;t<n;t++)this.expandByPoint(e[t]);return this}setFromCenterAndSize(e,t){let n=di.copy(t).multiplyScalar(.5);return this.min.copy(e).sub(n),this.max.copy(e).add(n),this}setFromObject(e,t=!1){return this.makeEmpty(),this.expandByObject(e,t)}clone(){return new this.constructor().copy(this)}copy(e){return this.min.copy(e.min),this.max.copy(e.max),this}makeEmpty(){return this.min.x=this.min.y=this.min.z=1/0,this.max.x=this.max.y=this.max.z=-1/0,this}isEmpty(){return this.max.x<this.min.x||this.max.y<this.min.y||this.max.z<this.min.z}getCenter(e){return this.isEmpty()?e.set(0,0,0):e.addVectors(this.min,this.max).multiplyScalar(.5)}getSize(e){return this.isEmpty()?e.set(0,0,0):e.subVectors(this.max,this.min)}expandByPoint(e){return this.min.min(e),this.max.max(e),this}expandByVector(e){return this.min.sub(e),this.max.add(e),this}expandByScalar(e){return this.min.addScalar(-e),this.max.addScalar(e),this}expandByObject(e,t=!1){e.updateWorldMatrix(!1,!1);let n=e.geometry;if(n!==void 0){let s=n.getAttribute("position");if(t===!0&&s!==void 0&&e.isInstancedMesh!==!0)for(let a=0,o=s.count;a<o;a++)e.isMesh===!0?e.getVertexPosition(a,di):di.fromBufferAttribute(s,a),di.applyMatrix4(e.matrixWorld),this.expandByPoint(di);else e.boundingBox!==void 0?(e.boundingBox===null&&e.computeBoundingBox(),wo.copy(e.boundingBox)):(n.boundingBox===null&&n.computeBoundingBox(),wo.copy(n.boundingBox)),wo.applyMatrix4(e.matrixWorld),this.union(wo)}let i=e.children;for(let s=0,a=i.length;s<a;s++)this.expandByObject(i[s],t);return this}containsPoint(e){return e.x>=this.min.x&&e.x<=this.max.x&&e.y>=this.min.y&&e.y<=this.max.y&&e.z>=this.min.z&&e.z<=this.max.z}containsBox(e){return this.min.x<=e.min.x&&e.max.x<=this.max.x&&this.min.y<=e.min.y&&e.max.y<=this.max.y&&this.min.z<=e.min.z&&e.max.z<=this.max.z}getParameter(e,t){return t.set((e.x-this.min.x)/(this.max.x-this.min.x),(e.y-this.min.y)/(this.max.y-this.min.y),(e.z-this.min.z)/(this.max.z-this.min.z))}intersectsBox(e){return e.max.x>=this.min.x&&e.min.x<=this.max.x&&e.max.y>=this.min.y&&e.min.y<=this.max.y&&e.max.z>=this.min.z&&e.min.z<=this.max.z}intersectsSphere(e){return this.clampPoint(e.center,di),di.distanceToSquared(e.center)<=e.radius*e.radius}intersectsPlane(e){let t,n;return e.normal.x>0?(t=e.normal.x*this.min.x,n=e.normal.x*this.max.x):(t=e.normal.x*this.max.x,n=e.normal.x*this.min.x),e.normal.y>0?(t+=e.normal.y*this.min.y,n+=e.normal.y*this.max.y):(t+=e.normal.y*this.max.y,n+=e.normal.y*this.min.y),e.normal.z>0?(t+=e.normal.z*this.min.z,n+=e.normal.z*this.max.z):(t+=e.normal.z*this.max.z,n+=e.normal.z*this.min.z),t<=-e.constant&&n>=-e.constant}intersectsTriangle(e){if(this.isEmpty())return!1;this.getCenter(oa),Ao.subVectors(this.max,oa),ss.subVectors(e.a,oa),as.subVectors(e.b,oa),os.subVectors(e.c,oa),er.subVectors(as,ss),tr.subVectors(os,as),Tr.subVectors(ss,os);let t=[0,-er.z,er.y,0,-tr.z,tr.y,0,-Tr.z,Tr.y,er.z,0,-er.x,tr.z,0,-tr.x,Tr.z,0,-Tr.x,-er.y,er.x,0,-tr.y,tr.x,0,-Tr.y,Tr.x,0];return!!ih(t,ss,as,os,Ao)&&(t=[1,0,0,0,1,0,0,0,1],!!ih(t,ss,as,os,Ao)&&(Co.crossVectors(er,tr),t=[Co.x,Co.y,Co.z],ih(t,ss,as,os,Ao)))}clampPoint(e,t){return t.copy(e).clamp(this.min,this.max)}distanceToPoint(e){return this.clampPoint(e,di).distanceTo(e)}getBoundingSphere(e){return this.isEmpty()?e.makeEmpty():(this.getCenter(e.center),e.radius=.5*this.getSize(di).length()),e}intersect(e){return this.min.max(e.min),this.max.min(e.max),this.isEmpty()&&this.makeEmpty(),this}union(e){return this.min.min(e.min),this.max.max(e.max),this}applyMatrix4(e){return this.isEmpty()||(Gi[0].set(this.min.x,this.min.y,this.min.z).applyMatrix4(e),Gi[1].set(this.min.x,this.min.y,this.max.z).applyMatrix4(e),Gi[2].set(this.min.x,this.max.y,this.min.z).applyMatrix4(e),Gi[3].set(this.min.x,this.max.y,this.max.z).applyMatrix4(e),Gi[4].set(this.max.x,this.min.y,this.min.z).applyMatrix4(e),Gi[5].set(this.max.x,this.min.y,this.max.z).applyMatrix4(e),Gi[6].set(this.max.x,this.max.y,this.min.z).applyMatrix4(e),Gi[7].set(this.max.x,this.max.y,this.max.z).applyMatrix4(e),this.setFromPoints(Gi)),this}translate(e){return this.min.add(e),this.max.add(e),this}equals(e){return e.min.equals(this.min)&&e.max.equals(this.max)}toJSON(){return{min:this.min.toArray(),max:this.max.toArray()}}fromJSON(e){return this.min.fromArray(e.min),this.max.fromArray(e.max),this}},Gi=[new N,new N,new N,new N,new N,new N,new N,new N],di=new N,wo=new ln,ss=new N,as=new N,os=new N,er=new N,tr=new N,Tr=new N,oa=new N,Ao=new N,Co=new N,Er=new N;function ih(r,e,t,n,i){for(let s=0,a=r.length-3;s<=a;s+=3){Er.fromArray(r,s);let o=i.x*Math.abs(Er.x)+i.y*Math.abs(Er.y)+i.z*Math.abs(Er.z),c=e.dot(Er),l=t.dot(Er),h=n.dot(Er);if(Math.max(-Math.max(c,l,h),Math.min(c,l,h))>o)return!1}return!0}var Hg=xm();function xm(){let r=new ArrayBuffer(4),e=new Float32Array(r),t=new Uint32Array(r),n=new Uint32Array(512),i=new Uint32Array(512);for(let c=0;c<256;++c){let l=c-127;l<-27?(n[c]=0,n[256|c]=32768,i[c]=24,i[256|c]=24):l<-14?(n[c]=1024>>-l-14,n[256|c]=1024>>-l-14|32768,i[c]=-l-1,i[256|c]=-l-1):l<=15?(n[c]=l+15<<10,n[256|c]=l+15<<10|32768,i[c]=13,i[256|c]=13):l<128?(n[c]=31744,n[256|c]=64512,i[c]=24,i[256|c]=24):(n[c]=31744,n[256|c]=64512,i[c]=13,i[256|c]=13)}let s=new Uint32Array(2048),a=new Uint32Array(64),o=new Uint32Array(64);for(let c=1;c<1024;++c){let l=c<<13,h=0;for(;!(8388608&l);)l<<=1,h-=8388608;l&=-8388609,h+=947912704,s[c]=l|h}for(let c=1024;c<2048;++c)s[c]=939524096+(c-1024<<13);for(let c=1;c<31;++c)a[c]=c<<23;a[31]=1199570944,a[32]=2147483648;for(let c=33;c<63;++c)a[c]=2147483648+(c-32<<23);a[63]=3347054592;for(let c=1;c<64;++c)c!==32&&(o[c]=1024);return{floatView:e,uint32View:t,baseTable:n,shiftTable:i,mantissaTable:s,exponentTable:a,offsetTable:o}}var jt=new N,Ro=new Te,Mm=0,on=class{constructor(e,t,n=!1){if(Array.isArray(e))throw new TypeError("THREE.BufferAttribute: array should be a Typed Array.");this.isBufferAttribute=!0,Object.defineProperty(this,"id",{value:Mm++}),this.name="",this.array=e,this.itemSize=t,this.count=e!==void 0?e.length/t:0,this.normalized=n,this.usage=bh,this.updateRanges=[],this.gpuType=si,this.version=0}onUploadCallback(){}set needsUpdate(e){e===!0&&this.version++}setUsage(e){return this.usage=e,this}addUpdateRange(e,t){this.updateRanges.push({start:e,count:t})}clearUpdateRanges(){this.updateRanges.length=0}copy(e){return this.name=e.name,this.array=new e.array.constructor(e.array),this.itemSize=e.itemSize,this.count=e.count,this.normalized=e.normalized,this.usage=e.usage,this.gpuType=e.gpuType,this}copyAt(e,t,n){e*=this.itemSize,n*=t.itemSize;for(let i=0,s=this.itemSize;i<s;i++)this.array[e+i]=t.array[n+i];return this}copyArray(e){return this.array.set(e),this}applyMatrix3(e){if(this.itemSize===2)for(let t=0,n=this.count;t<n;t++)Ro.fromBufferAttribute(this,t),Ro.applyMatrix3(e),this.setXY(t,Ro.x,Ro.y);else if(this.itemSize===3)for(let t=0,n=this.count;t<n;t++)jt.fromBufferAttribute(this,t),jt.applyMatrix3(e),this.setXYZ(t,jt.x,jt.y,jt.z);return this}applyMatrix4(e){for(let t=0,n=this.count;t<n;t++)jt.fromBufferAttribute(this,t),jt.applyMatrix4(e),this.setXYZ(t,jt.x,jt.y,jt.z);return this}applyNormalMatrix(e){for(let t=0,n=this.count;t<n;t++)jt.fromBufferAttribute(this,t),jt.applyNormalMatrix(e),this.setXYZ(t,jt.x,jt.y,jt.z);return this}transformDirection(e){for(let t=0,n=this.count;t<n;t++)jt.fromBufferAttribute(this,t),jt.transformDirection(e),this.setXYZ(t,jt.x,jt.y,jt.z);return this}set(e,t=0){return this.array.set(e,t),this}getComponent(e,t){let n=this.array[e*this.itemSize+t];return this.normalized&&(n=ds(n,this.array)),n}setComponent(e,t,n){return this.normalized&&(n=Sn(n,this.array)),this.array[e*this.itemSize+t]=n,this}getX(e){let t=this.array[e*this.itemSize];return this.normalized&&(t=ds(t,this.array)),t}setX(e,t){return this.normalized&&(t=Sn(t,this.array)),this.array[e*this.itemSize]=t,this}getY(e){let t=this.array[e*this.itemSize+1];return this.normalized&&(t=ds(t,this.array)),t}setY(e,t){return this.normalized&&(t=Sn(t,this.array)),this.array[e*this.itemSize+1]=t,this}getZ(e){let t=this.array[e*this.itemSize+2];return this.normalized&&(t=ds(t,this.array)),t}setZ(e,t){return this.normalized&&(t=Sn(t,this.array)),this.array[e*this.itemSize+2]=t,this}getW(e){let t=this.array[e*this.itemSize+3];return this.normalized&&(t=ds(t,this.array)),t}setW(e,t){return this.normalized&&(t=Sn(t,this.array)),this.array[e*this.itemSize+3]=t,this}setXY(e,t,n){return e*=this.itemSize,this.normalized&&(t=Sn(t,this.array),n=Sn(n,this.array)),this.array[e+0]=t,this.array[e+1]=n,this}setXYZ(e,t,n,i){return e*=this.itemSize,this.normalized&&(t=Sn(t,this.array),n=Sn(n,this.array),i=Sn(i,this.array)),this.array[e+0]=t,this.array[e+1]=n,this.array[e+2]=i,this}setXYZW(e,t,n,i,s){return e*=this.itemSize,this.normalized&&(t=Sn(t,this.array),n=Sn(n,this.array),i=Sn(i,this.array),s=Sn(s,this.array)),this.array[e+0]=t,this.array[e+1]=n,this.array[e+2]=i,this.array[e+3]=s,this}onUpload(e){return this.onUploadCallback=e,this}clone(){return new this.constructor(this.array,this.itemSize).copy(this)}toJSON(){let e={itemSize:this.itemSize,type:this.array.constructor.name,array:Array.from(this.array),normalized:this.normalized};return this.name!==""&&(e.name=this.name),this.usage!==bh&&(e.usage=this.usage),e}};var Sa=class extends on{constructor(e,t,n){super(new Uint16Array(e),t,n)}};var Ta=class extends on{constructor(e,t,n){super(new Uint32Array(e),t,n)}};var $e=class extends on{constructor(e,t,n){super(new Float32Array(e),t,n)}},bm=new ln,la=new N,rh=new N,ii=class{constructor(e=new N,t=-1){this.isSphere=!0,this.center=e,this.radius=t}set(e,t){return this.center.copy(e),this.radius=t,this}setFromPoints(e,t){let n=this.center;t!==void 0?n.copy(t):bm.setFromPoints(e).getCenter(n);let i=0;for(let s=0,a=e.length;s<a;s++)i=Math.max(i,n.distanceToSquared(e[s]));return this.radius=Math.sqrt(i),this}copy(e){return this.center.copy(e.center),this.radius=e.radius,this}isEmpty(){return this.radius<0}makeEmpty(){return this.center.set(0,0,0),this.radius=-1,this}containsPoint(e){return e.distanceToSquared(this.center)<=this.radius*this.radius}distanceToPoint(e){return e.distanceTo(this.center)-this.radius}intersectsSphere(e){let t=this.radius+e.radius;return e.center.distanceToSquared(this.center)<=t*t}intersectsBox(e){return e.intersectsSphere(this)}intersectsPlane(e){return Math.abs(e.distanceToPoint(this.center))<=this.radius}clampPoint(e,t){let n=this.center.distanceToSquared(e);return t.copy(e),n>this.radius*this.radius&&(t.sub(this.center).normalize(),t.multiplyScalar(this.radius).add(this.center)),t}getBoundingBox(e){return this.isEmpty()?(e.makeEmpty(),e):(e.set(this.center,this.center),e.expandByScalar(this.radius),e)}applyMatrix4(e){return this.center.applyMatrix4(e),this.radius=this.radius*e.getMaxScaleOnAxis(),this}translate(e){return this.center.add(e),this}expandByPoint(e){if(this.isEmpty())return this.center.copy(e),this.radius=0,this;la.subVectors(e,this.center);let t=la.lengthSq();if(t>this.radius*this.radius){let n=Math.sqrt(t),i=.5*(n-this.radius);this.center.addScaledVector(la,i/n),this.radius+=i}return this}union(e){return e.isEmpty()?this:this.isEmpty()?(this.copy(e),this):(this.center.equals(e.center)===!0?this.radius=Math.max(this.radius,e.radius):(rh.subVectors(e.center,this.center).setLength(e.radius),this.expandByPoint(la.copy(e.center).add(rh)),this.expandByPoint(la.copy(e.center).sub(rh))),this)}equals(e){return e.center.equals(this.center)&&e.radius===this.radius}clone(){return new this.constructor().copy(this)}toJSON(){return{radius:this.radius,center:this.center.toArray()}}fromJSON(e){return this.radius=e.radius,this.center.fromArray(e.center),this}},Sm=0,ti=new st,sh=new Gt,ls=new N,Hn=new ln,ca=new ln,tn=new N,St=class r extends bi{constructor(){super(),this.isBufferGeometry=!0,Object.defineProperty(this,"id",{value:Sm++}),this.uuid=Br(),this.name="",this.type="BufferGeometry",this.index=null,this.indirect=null,this.indirectOffset=0,this.attributes={},this.morphAttributes={},this.morphTargetsRelative=!1,this.groups=[],this.boundingBox=null,this.boundingSphere=null,this.drawRange={start:0,count:1/0},this.userData={}}getIndex(){return this.index}setIndex(e){return Array.isArray(e)?this.index=new(function(t){for(let n=t.length-1;n>=0;--n)if(t[n]>=65535)return!0;return!1}(e)?Ta:Sa)(e,1):this.index=e,this}setIndirect(e,t=0){return this.indirect=e,this.indirectOffset=t,this}getIndirect(){return this.indirect}getAttribute(e){return this.attributes[e]}setAttribute(e,t){return this.attributes[e]=t,this}deleteAttribute(e){return delete this.attributes[e],this}hasAttribute(e){return this.attributes[e]!==void 0}addGroup(e,t,n=0){this.groups.push({start:e,count:t,materialIndex:n})}clearGroups(){this.groups=[]}setDrawRange(e,t){this.drawRange.start=e,this.drawRange.count=t}applyMatrix4(e){let t=this.attributes.position;t!==void 0&&(t.applyMatrix4(e),t.needsUpdate=!0);let n=this.attributes.normal;if(n!==void 0){let s=new ht().getNormalMatrix(e);n.applyNormalMatrix(s),n.needsUpdate=!0}let i=this.attributes.tangent;return i!==void 0&&(i.transformDirection(e),i.needsUpdate=!0),this.boundingBox!==null&&this.computeBoundingBox(),this.boundingSphere!==null&&this.computeBoundingSphere(),this}applyQuaternion(e){return ti.makeRotationFromQuaternion(e),this.applyMatrix4(ti),this}rotateX(e){return ti.makeRotationX(e),this.applyMatrix4(ti),this}rotateY(e){return ti.makeRotationY(e),this.applyMatrix4(ti),this}rotateZ(e){return ti.makeRotationZ(e),this.applyMatrix4(ti),this}translate(e,t,n){return ti.makeTranslation(e,t,n),this.applyMatrix4(ti),this}scale(e,t,n){return ti.makeScale(e,t,n),this.applyMatrix4(ti),this}lookAt(e){return sh.lookAt(e),sh.updateMatrix(),this.applyMatrix4(sh.matrix),this}center(){return this.computeBoundingBox(),this.boundingBox.getCenter(ls).negate(),this.translate(ls.x,ls.y,ls.z),this}setFromPoints(e){let t=this.getAttribute("position");if(t===void 0){let n=[];for(let i=0,s=e.length;i<s;i++){let a=e[i];n.push(a.x,a.y,a.z||0)}this.setAttribute("position",new $e(n,3))}else{let n=Math.min(e.length,t.count);for(let i=0;i<n;i++){let s=e[i];t.setXYZ(i,s.x,s.y,s.z||0)}e.length>t.count&&tt("BufferGeometry: Buffer size too small for points data. Use .dispose() and create a new geometry."),t.needsUpdate=!0}return this}computeBoundingBox(){this.boundingBox===null&&(this.boundingBox=new ln);let e=this.attributes.position,t=this.morphAttributes.position;if(e&&e.isGLBufferAttribute)return rt("BufferGeometry.computeBoundingBox(): GLBufferAttribute requires a manual bounding box.",this),void this.boundingBox.set(new N(-1/0,-1/0,-1/0),new N(1/0,1/0,1/0));if(e!==void 0){if(this.boundingBox.setFromBufferAttribute(e),t)for(let n=0,i=t.length;n<i;n++){let s=t[n];Hn.setFromBufferAttribute(s),this.morphTargetsRelative?(tn.addVectors(this.boundingBox.min,Hn.min),this.boundingBox.expandByPoint(tn),tn.addVectors(this.boundingBox.max,Hn.max),this.boundingBox.expandByPoint(tn)):(this.boundingBox.expandByPoint(Hn.min),this.boundingBox.expandByPoint(Hn.max))}}else this.boundingBox.makeEmpty();(isNaN(this.boundingBox.min.x)||isNaN(this.boundingBox.min.y)||isNaN(this.boundingBox.min.z))&&rt('BufferGeometry.computeBoundingBox(): Computed min/max have NaN values. The "position" attribute is likely to have NaN values.',this)}computeBoundingSphere(){this.boundingSphere===null&&(this.boundingSphere=new ii);let e=this.attributes.position,t=this.morphAttributes.position;if(e&&e.isGLBufferAttribute)return rt("BufferGeometry.computeBoundingSphere(): GLBufferAttribute requires a manual bounding sphere.",this),void this.boundingSphere.set(new N,1/0);if(e){let n=this.boundingSphere.center;if(Hn.setFromBufferAttribute(e),t)for(let s=0,a=t.length;s<a;s++){let o=t[s];ca.setFromBufferAttribute(o),this.morphTargetsRelative?(tn.addVectors(Hn.min,ca.min),Hn.expandByPoint(tn),tn.addVectors(Hn.max,ca.max),Hn.expandByPoint(tn)):(Hn.expandByPoint(ca.min),Hn.expandByPoint(ca.max))}Hn.getCenter(n);let i=0;for(let s=0,a=e.count;s<a;s++)tn.fromBufferAttribute(e,s),i=Math.max(i,n.distanceToSquared(tn));if(t)for(let s=0,a=t.length;s<a;s++){let o=t[s],c=this.morphTargetsRelative;for(let l=0,h=o.count;l<h;l++)tn.fromBufferAttribute(o,l),c&&(ls.fromBufferAttribute(e,l),tn.add(ls)),i=Math.max(i,n.distanceToSquared(tn))}this.boundingSphere.radius=Math.sqrt(i),isNaN(this.boundingSphere.radius)&&rt('BufferGeometry.computeBoundingSphere(): Computed radius is NaN. The "position" attribute is likely to have NaN values.',this)}}computeTangents(){let e=this.index,t=this.attributes;if(e===null||t.position===void 0||t.normal===void 0||t.uv===void 0)return void rt("BufferGeometry: .computeTangents() failed. Missing required attributes (index, position, normal or uv)");let n=t.position,i=t.normal,s=t.uv;this.hasAttribute("tangent")===!1&&this.setAttribute("tangent",new on(new Float32Array(4*n.count),4));let a=this.getAttribute("tangent"),o=[],c=[];for(let X=0;X<n.count;X++)o[X]=new N,c[X]=new N;let l=new N,h=new N,p=new N,f=new Te,m=new Te,v=new Te,g=new N,u=new N;function y(X,H,se){l.fromBufferAttribute(n,X),h.fromBufferAttribute(n,H),p.fromBufferAttribute(n,se),f.fromBufferAttribute(s,X),m.fromBufferAttribute(s,H),v.fromBufferAttribute(s,se),h.sub(l),p.sub(l),m.sub(f),v.sub(f);let oe=1/(m.x*v.y-v.x*m.y);isFinite(oe)&&(g.copy(h).multiplyScalar(v.y).addScaledVector(p,-m.y).multiplyScalar(oe),u.copy(p).multiplyScalar(m.x).addScaledVector(h,-v.x).multiplyScalar(oe),o[X].add(g),o[H].add(g),o[se].add(g),c[X].add(u),c[H].add(u),c[se].add(u))}let x=this.groups;x.length===0&&(x=[{start:0,count:e.count}]);for(let X=0,H=x.length;X<H;++X){let se=x[X],oe=se.start;for(let te=oe,ne=oe+se.count;te<ne;te+=3)y(e.getX(te+0),e.getX(te+1),e.getX(te+2))}let S=new N,E=new N,R=new N,P=new N;function V(X){R.fromBufferAttribute(i,X),P.copy(R);let H=o[X];S.copy(H),S.sub(R.multiplyScalar(R.dot(H))).normalize(),E.crossVectors(P,H);let se=E.dot(c[X])<0?-1:1;a.setXYZW(X,S.x,S.y,S.z,se)}for(let X=0,H=x.length;X<H;++X){let se=x[X],oe=se.start;for(let te=oe,ne=oe+se.count;te<ne;te+=3)V(e.getX(te+0)),V(e.getX(te+1)),V(e.getX(te+2))}}computeVertexNormals(){let e=this.index,t=this.getAttribute("position");if(t!==void 0){let n=this.getAttribute("normal");if(n===void 0)n=new on(new Float32Array(3*t.count),3),this.setAttribute("normal",n);else for(let f=0,m=n.count;f<m;f++)n.setXYZ(f,0,0,0);let i=new N,s=new N,a=new N,o=new N,c=new N,l=new N,h=new N,p=new N;if(e)for(let f=0,m=e.count;f<m;f+=3){let v=e.getX(f+0),g=e.getX(f+1),u=e.getX(f+2);i.fromBufferAttribute(t,v),s.fromBufferAttribute(t,g),a.fromBufferAttribute(t,u),h.subVectors(a,s),p.subVectors(i,s),h.cross(p),o.fromBufferAttribute(n,v),c.fromBufferAttribute(n,g),l.fromBufferAttribute(n,u),o.add(h),c.add(h),l.add(h),n.setXYZ(v,o.x,o.y,o.z),n.setXYZ(g,c.x,c.y,c.z),n.setXYZ(u,l.x,l.y,l.z)}else for(let f=0,m=t.count;f<m;f+=3)i.fromBufferAttribute(t,f+0),s.fromBufferAttribute(t,f+1),a.fromBufferAttribute(t,f+2),h.subVectors(a,s),p.subVectors(i,s),h.cross(p),n.setXYZ(f+0,h.x,h.y,h.z),n.setXYZ(f+1,h.x,h.y,h.z),n.setXYZ(f+2,h.x,h.y,h.z);this.normalizeNormals(),n.needsUpdate=!0}}normalizeNormals(){let e=this.attributes.normal;for(let t=0,n=e.count;t<n;t++)tn.fromBufferAttribute(e,t),tn.normalize(),e.setXYZ(t,tn.x,tn.y,tn.z)}toNonIndexed(){function e(o,c){let l=o.array,h=o.itemSize,p=o.normalized,f=new l.constructor(c.length*h),m=0,v=0;for(let g=0,u=c.length;g<u;g++){m=o.isInterleavedBufferAttribute?c[g]*o.data.stride+o.offset:c[g]*h;for(let y=0;y<h;y++)f[v++]=l[m++]}return new on(f,h,p)}if(this.index===null)return tt("BufferGeometry.toNonIndexed(): BufferGeometry is already non-indexed."),this;let t=new r,n=this.index.array,i=this.attributes;for(let o in i){let c=e(i[o],n);t.setAttribute(o,c)}let s=this.morphAttributes;for(let o in s){let c=[],l=s[o];for(let h=0,p=l.length;h<p;h++){let f=e(l[h],n);c.push(f)}t.morphAttributes[o]=c}t.morphTargetsRelative=this.morphTargetsRelative;let a=this.groups;for(let o=0,c=a.length;o<c;o++){let l=a[o];t.addGroup(l.start,l.count,l.materialIndex)}return t}toJSON(){let e={metadata:{version:4.7,type:"BufferGeometry",generator:"BufferGeometry.toJSON"}};if(e.uuid=this.uuid,e.type=this.type,this.name!==""&&(e.name=this.name),Object.keys(this.userData).length>0&&(e.userData=this.userData),this.parameters!==void 0){let c=this.parameters;for(let l in c)c[l]!==void 0&&(e[l]=c[l]);return e}e.data={attributes:{}};let t=this.index;t!==null&&(e.data.index={type:t.array.constructor.name,array:Array.prototype.slice.call(t.array)});let n=this.attributes;for(let c in n){let l=n[c];e.data.attributes[c]=l.toJSON(e.data)}let i={},s=!1;for(let c in this.morphAttributes){let l=this.morphAttributes[c],h=[];for(let p=0,f=l.length;p<f;p++){let m=l[p];h.push(m.toJSON(e.data))}h.length>0&&(i[c]=h,s=!0)}s&&(e.data.morphAttributes=i,e.data.morphTargetsRelative=this.morphTargetsRelative);let a=this.groups;a.length>0&&(e.data.groups=JSON.parse(JSON.stringify(a)));let o=this.boundingSphere;return o!==null&&(e.data.boundingSphere=o.toJSON()),e}clone(){return new this.constructor().copy(this)}copy(e){this.index=null,this.attributes={},this.morphAttributes={},this.groups=[],this.boundingBox=null,this.boundingSphere=null;let t={};this.name=e.name;let n=e.index;n!==null&&this.setIndex(n.clone());let i=e.attributes;for(let l in i){let h=i[l];this.setAttribute(l,h.clone(t))}let s=e.morphAttributes;for(let l in s){let h=[],p=s[l];for(let f=0,m=p.length;f<m;f++)h.push(p[f].clone(t));this.morphAttributes[l]=h}this.morphTargetsRelative=e.morphTargetsRelative;let a=e.groups;for(let l=0,h=a.length;l<h;l++){let p=a[l];this.addGroup(p.start,p.count,p.materialIndex)}let o=e.boundingBox;o!==null&&(this.boundingBox=o.clone());let c=e.boundingSphere;return c!==null&&(this.boundingSphere=c.clone()),this.drawRange.start=e.drawRange.start,this.drawRange.count=e.drawRange.count,this.userData=e.userData,this}dispose(){this.dispatchEvent({type:"dispose"})}};var Gg=new N;var Tm=0,Si=class extends bi{constructor(){super(),this.isMaterial=!0,Object.defineProperty(this,"id",{value:Tm++}),this.uuid=Br(),this.name="",this.type="Material",this.blending=1,this.side=0,this.vertexColors=!1,this.opacity=1,this.transparent=!1,this.alphaHash=!1,this.blendSrc=204,this.blendDst=205,this.blendEquation=100,this.blendSrcAlpha=null,this.blendDstAlpha=null,this.blendEquationAlpha=null,this.blendColor=new ke(0,0,0),this.blendAlpha=0,this.depthFunc=3,this.depthTest=!0,this.depthWrite=!0,this.stencilWriteMask=255,this.stencilFunc=519,this.stencilRef=0,this.stencilFuncMask=255,this.stencilFail=Cr,this.stencilZFail=Cr,this.stencilZPass=Cr,this.stencilWrite=!1,this.clippingPlanes=null,this.clipIntersection=!1,this.clipShadows=!1,this.shadowSide=null,this.colorWrite=!0,this.precision=null,this.polygonOffset=!1,this.polygonOffsetFactor=0,this.polygonOffsetUnits=0,this.dithering=!1,this.alphaToCoverage=!1,this.premultipliedAlpha=!1,this.forceSinglePass=!1,this.allowOverride=!0,this.visible=!0,this.toneMapped=!0,this.userData={},this.version=0,this._alphaTest=0}get alphaTest(){return this._alphaTest}set alphaTest(e){this._alphaTest>0!=e>0&&this.version++,this._alphaTest=e}onBeforeRender(){}onBeforeCompile(){}customProgramCacheKey(){return this.onBeforeCompile.toString()}setValues(e){if(e!==void 0)for(let t in e){let n=e[t];if(n===void 0){tt(`Material: parameter '${t}' has value of undefined.`);continue}let i=this[t];i!==void 0?i&&i.isColor?i.set(n):i&&i.isVector3&&n&&n.isVector3?i.copy(n):this[t]=n:tt(`Material: '${t}' is not a property of THREE.${this.type}.`)}}toJSON(e){let t=e===void 0||typeof e=="string";t&&(e={textures:{},images:{}});let n={metadata:{version:4.7,type:"Material",generator:"Material.toJSON"}};function i(s){let a=[];for(let o in s){let c=s[o];delete c.metadata,a.push(c)}return a}if(n.uuid=this.uuid,n.type=this.type,this.name!==""&&(n.name=this.name),this.color&&this.color.isColor&&(n.color=this.color.getHex()),this.roughness!==void 0&&(n.roughness=this.roughness),this.metalness!==void 0&&(n.metalness=this.metalness),this.sheen!==void 0&&(n.sheen=this.sheen),this.sheenColor&&this.sheenColor.isColor&&(n.sheenColor=this.sheenColor.getHex()),this.sheenRoughness!==void 0&&(n.sheenRoughness=this.sheenRoughness),this.emissive&&this.emissive.isColor&&(n.emissive=this.emissive.getHex()),this.emissiveIntensity!==void 0&&this.emissiveIntensity!==1&&(n.emissiveIntensity=this.emissiveIntensity),this.specular&&this.specular.isColor&&(n.specular=this.specular.getHex()),this.specularIntensity!==void 0&&(n.specularIntensity=this.specularIntensity),this.specularColor&&this.specularColor.isColor&&(n.specularColor=this.specularColor.getHex()),this.shininess!==void 0&&(n.shininess=this.shininess),this.clearcoat!==void 0&&(n.clearcoat=this.clearcoat),this.clearcoatRoughness!==void 0&&(n.clearcoatRoughness=this.clearcoatRoughness),this.clearcoatMap&&this.clearcoatMap.isTexture&&(n.clearcoatMap=this.clearcoatMap.toJSON(e).uuid),this.clearcoatRoughnessMap&&this.clearcoatRoughnessMap.isTexture&&(n.clearcoatRoughnessMap=this.clearcoatRoughnessMap.toJSON(e).uuid),this.clearcoatNormalMap&&this.clearcoatNormalMap.isTexture&&(n.clearcoatNormalMap=this.clearcoatNormalMap.toJSON(e).uuid,n.clearcoatNormalScale=this.clearcoatNormalScale.toArray()),this.sheenColorMap&&this.sheenColorMap.isTexture&&(n.sheenColorMap=this.sheenColorMap.toJSON(e).uuid),this.sheenRoughnessMap&&this.sheenRoughnessMap.isTexture&&(n.sheenRoughnessMap=this.sheenRoughnessMap.toJSON(e).uuid),this.dispersion!==void 0&&(n.dispersion=this.dispersion),this.iridescence!==void 0&&(n.iridescence=this.iridescence),this.iridescenceIOR!==void 0&&(n.iridescenceIOR=this.iridescenceIOR),this.iridescenceThicknessRange!==void 0&&(n.iridescenceThicknessRange=this.iridescenceThicknessRange),this.iridescenceMap&&this.iridescenceMap.isTexture&&(n.iridescenceMap=this.iridescenceMap.toJSON(e).uuid),this.iridescenceThicknessMap&&this.iridescenceThicknessMap.isTexture&&(n.iridescenceThicknessMap=this.iridescenceThicknessMap.toJSON(e).uuid),this.anisotropy!==void 0&&(n.anisotropy=this.anisotropy),this.anisotropyRotation!==void 0&&(n.anisotropyRotation=this.anisotropyRotation),this.anisotropyMap&&this.anisotropyMap.isTexture&&(n.anisotropyMap=this.anisotropyMap.toJSON(e).uuid),this.map&&this.map.isTexture&&(n.map=this.map.toJSON(e).uuid),this.matcap&&this.matcap.isTexture&&(n.matcap=this.matcap.toJSON(e).uuid),this.alphaMap&&this.alphaMap.isTexture&&(n.alphaMap=this.alphaMap.toJSON(e).uuid),this.lightMap&&this.lightMap.isTexture&&(n.lightMap=this.lightMap.toJSON(e).uuid,n.lightMapIntensity=this.lightMapIntensity),this.aoMap&&this.aoMap.isTexture&&(n.aoMap=this.aoMap.toJSON(e).uuid,n.aoMapIntensity=this.aoMapIntensity),this.bumpMap&&this.bumpMap.isTexture&&(n.bumpMap=this.bumpMap.toJSON(e).uuid,n.bumpScale=this.bumpScale),this.normalMap&&this.normalMap.isTexture&&(n.normalMap=this.normalMap.toJSON(e).uuid,n.normalMapType=this.normalMapType,n.normalScale=this.normalScale.toArray()),this.displacementMap&&this.displacementMap.isTexture&&(n.displacementMap=this.displacementMap.toJSON(e).uuid,n.displacementScale=this.displacementScale,n.displacementBias=this.displacementBias),this.roughnessMap&&this.roughnessMap.isTexture&&(n.roughnessMap=this.roughnessMap.toJSON(e).uuid),this.metalnessMap&&this.metalnessMap.isTexture&&(n.metalnessMap=this.metalnessMap.toJSON(e).uuid),this.emissiveMap&&this.emissiveMap.isTexture&&(n.emissiveMap=this.emissiveMap.toJSON(e).uuid),this.specularMap&&this.specularMap.isTexture&&(n.specularMap=this.specularMap.toJSON(e).uuid),this.specularIntensityMap&&this.specularIntensityMap.isTexture&&(n.specularIntensityMap=this.specularIntensityMap.toJSON(e).uuid),this.specularColorMap&&this.specularColorMap.isTexture&&(n.specularColorMap=this.specularColorMap.toJSON(e).uuid),this.envMap&&this.envMap.isTexture&&(n.envMap=this.envMap.toJSON(e).uuid,this.combine!==void 0&&(n.combine=this.combine)),this.envMapRotation!==void 0&&(n.envMapRotation=this.envMapRotation.toArray()),this.envMapIntensity!==void 0&&(n.envMapIntensity=this.envMapIntensity),this.reflectivity!==void 0&&(n.reflectivity=this.reflectivity),this.refractionRatio!==void 0&&(n.refractionRatio=this.refractionRatio),this.gradientMap&&this.gradientMap.isTexture&&(n.gradientMap=this.gradientMap.toJSON(e).uuid),this.transmission!==void 0&&(n.transmission=this.transmission),this.transmissionMap&&this.transmissionMap.isTexture&&(n.transmissionMap=this.transmissionMap.toJSON(e).uuid),this.thickness!==void 0&&(n.thickness=this.thickness),this.thicknessMap&&this.thicknessMap.isTexture&&(n.thicknessMap=this.thicknessMap.toJSON(e).uuid),this.attenuationDistance!==void 0&&this.attenuationDistance!==1/0&&(n.attenuationDistance=this.attenuationDistance),this.attenuationColor!==void 0&&(n.attenuationColor=this.attenuationColor.getHex()),this.size!==void 0&&(n.size=this.size),this.shadowSide!==null&&(n.shadowSide=this.shadowSide),this.sizeAttenuation!==void 0&&(n.sizeAttenuation=this.sizeAttenuation),this.blending!==1&&(n.blending=this.blending),this.side!==0&&(n.side=this.side),this.vertexColors===!0&&(n.vertexColors=!0),this.opacity<1&&(n.opacity=this.opacity),this.transparent===!0&&(n.transparent=!0),this.blendSrc!==204&&(n.blendSrc=this.blendSrc),this.blendDst!==205&&(n.blendDst=this.blendDst),this.blendEquation!==100&&(n.blendEquation=this.blendEquation),this.blendSrcAlpha!==null&&(n.blendSrcAlpha=this.blendSrcAlpha),this.blendDstAlpha!==null&&(n.blendDstAlpha=this.blendDstAlpha),this.blendEquationAlpha!==null&&(n.blendEquationAlpha=this.blendEquationAlpha),this.blendColor&&this.blendColor.isColor&&(n.blendColor=this.blendColor.getHex()),this.blendAlpha!==0&&(n.blendAlpha=this.blendAlpha),this.depthFunc!==3&&(n.depthFunc=this.depthFunc),this.depthTest===!1&&(n.depthTest=this.depthTest),this.depthWrite===!1&&(n.depthWrite=this.depthWrite),this.colorWrite===!1&&(n.colorWrite=this.colorWrite),this.stencilWriteMask!==255&&(n.stencilWriteMask=this.stencilWriteMask),this.stencilFunc!==519&&(n.stencilFunc=this.stencilFunc),this.stencilRef!==0&&(n.stencilRef=this.stencilRef),this.stencilFuncMask!==255&&(n.stencilFuncMask=this.stencilFuncMask),this.stencilFail!==Cr&&(n.stencilFail=this.stencilFail),this.stencilZFail!==Cr&&(n.stencilZFail=this.stencilZFail),this.stencilZPass!==Cr&&(n.stencilZPass=this.stencilZPass),this.stencilWrite===!0&&(n.stencilWrite=this.stencilWrite),this.rotation!==void 0&&this.rotation!==0&&(n.rotation=this.rotation),this.polygonOffset===!0&&(n.polygonOffset=!0),this.polygonOffsetFactor!==0&&(n.polygonOffsetFactor=this.polygonOffsetFactor),this.polygonOffsetUnits!==0&&(n.polygonOffsetUnits=this.polygonOffsetUnits),this.linewidth!==void 0&&this.linewidth!==1&&(n.linewidth=this.linewidth),this.dashSize!==void 0&&(n.dashSize=this.dashSize),this.gapSize!==void 0&&(n.gapSize=this.gapSize),this.scale!==void 0&&(n.scale=this.scale),this.dithering===!0&&(n.dithering=!0),this.alphaTest>0&&(n.alphaTest=this.alphaTest),this.alphaHash===!0&&(n.alphaHash=!0),this.alphaToCoverage===!0&&(n.alphaToCoverage=!0),this.premultipliedAlpha===!0&&(n.premultipliedAlpha=!0),this.forceSinglePass===!0&&(n.forceSinglePass=!0),this.allowOverride===!1&&(n.allowOverride=!1),this.wireframe===!0&&(n.wireframe=!0),this.wireframeLinewidth>1&&(n.wireframeLinewidth=this.wireframeLinewidth),this.wireframeLinecap!=="round"&&(n.wireframeLinecap=this.wireframeLinecap),this.wireframeLinejoin!=="round"&&(n.wireframeLinejoin=this.wireframeLinejoin),this.flatShading===!0&&(n.flatShading=!0),this.visible===!1&&(n.visible=!1),this.toneMapped===!1&&(n.toneMapped=!1),this.fog===!1&&(n.fog=!1),Object.keys(this.userData).length>0&&(n.userData=this.userData),t){let s=i(e.textures),a=i(e.images);s.length>0&&(n.textures=s),a.length>0&&(n.images=a)}return n}clone(){return new this.constructor().copy(this)}copy(e){this.name=e.name,this.blending=e.blending,this.side=e.side,this.vertexColors=e.vertexColors,this.opacity=e.opacity,this.transparent=e.transparent,this.blendSrc=e.blendSrc,this.blendDst=e.blendDst,this.blendEquation=e.blendEquation,this.blendSrcAlpha=e.blendSrcAlpha,this.blendDstAlpha=e.blendDstAlpha,this.blendEquationAlpha=e.blendEquationAlpha,this.blendColor.copy(e.blendColor),this.blendAlpha=e.blendAlpha,this.depthFunc=e.depthFunc,this.depthTest=e.depthTest,this.depthWrite=e.depthWrite,this.stencilWriteMask=e.stencilWriteMask,this.stencilFunc=e.stencilFunc,this.stencilRef=e.stencilRef,this.stencilFuncMask=e.stencilFuncMask,this.stencilFail=e.stencilFail,this.stencilZFail=e.stencilZFail,this.stencilZPass=e.stencilZPass,this.stencilWrite=e.stencilWrite;let t=e.clippingPlanes,n=null;if(t!==null){let i=t.length;n=new Array(i);for(let s=0;s!==i;++s)n[s]=t[s].clone()}return this.clippingPlanes=n,this.clipIntersection=e.clipIntersection,this.clipShadows=e.clipShadows,this.shadowSide=e.shadowSide,this.colorWrite=e.colorWrite,this.precision=e.precision,this.polygonOffset=e.polygonOffset,this.polygonOffsetFactor=e.polygonOffsetFactor,this.polygonOffsetUnits=e.polygonOffsetUnits,this.dithering=e.dithering,this.alphaTest=e.alphaTest,this.alphaHash=e.alphaHash,this.alphaToCoverage=e.alphaToCoverage,this.premultipliedAlpha=e.premultipliedAlpha,this.forceSinglePass=e.forceSinglePass,this.allowOverride=e.allowOverride,this.visible=e.visible,this.toneMapped=e.toneMapped,this.userData=JSON.parse(JSON.stringify(e.userData)),this}dispose(){this.dispatchEvent({type:"dispose"})}set needsUpdate(e){e===!0&&this.version++}};var Vg=new N,Wg=new N,jg=new N,Xg=new Te,qg=new Te,Yg=new st,Zg=new N,Jg=new N,Kg=new N,$g=new Te,Qg=new Te,e1=new Te;var t1=new N,n1=new N;var Vi=new N,ah=new N,Po=new N,nr=new N,oh=new N,Io=new N,lh=new N,Ti=class{constructor(e=new N,t=new N(0,0,-1)){this.origin=e,this.direction=t}set(e,t){return this.origin.copy(e),this.direction.copy(t),this}copy(e){return this.origin.copy(e.origin),this.direction.copy(e.direction),this}at(e,t){return t.copy(this.origin).addScaledVector(this.direction,e)}lookAt(e){return this.direction.copy(e).sub(this.origin).normalize(),this}recast(e){return this.origin.copy(this.at(e,Vi)),this}closestPointToPoint(e,t){t.subVectors(e,this.origin);let n=t.dot(this.direction);return n<0?t.copy(this.origin):t.copy(this.origin).addScaledVector(this.direction,n)}distanceToPoint(e){return Math.sqrt(this.distanceSqToPoint(e))}distanceSqToPoint(e){let t=Vi.subVectors(e,this.origin).dot(this.direction);return t<0?this.origin.distanceToSquared(e):(Vi.copy(this.origin).addScaledVector(this.direction,t),Vi.distanceToSquared(e))}distanceSqToSegment(e,t,n,i){ah.copy(e).add(t).multiplyScalar(.5),Po.copy(t).sub(e).normalize(),nr.copy(this.origin).sub(ah);let s=.5*e.distanceTo(t),a=-this.direction.dot(Po),o=nr.dot(this.direction),c=-nr.dot(Po),l=nr.lengthSq(),h=Math.abs(1-a*a),p,f,m,v;if(h>0)if(p=a*c-o,f=a*o-c,v=s*h,p>=0)if(f>=-v)if(f<=v){let g=1/h;p*=g,f*=g,m=p*(p+a*f+2*o)+f*(a*p+f+2*c)+l}else f=s,p=Math.max(0,-(a*f+o)),m=-p*p+f*(f+2*c)+l;else f=-s,p=Math.max(0,-(a*f+o)),m=-p*p+f*(f+2*c)+l;else f<=-v?(p=Math.max(0,-(-a*s+o)),f=p>0?-s:Math.min(Math.max(-s,-c),s),m=-p*p+f*(f+2*c)+l):f<=v?(p=0,f=Math.min(Math.max(-s,-c),s),m=f*(f+2*c)+l):(p=Math.max(0,-(a*s+o)),f=p>0?s:Math.min(Math.max(-s,-c),s),m=-p*p+f*(f+2*c)+l);else f=a>0?-s:s,p=Math.max(0,-(a*f+o)),m=-p*p+f*(f+2*c)+l;return n&&n.copy(this.origin).addScaledVector(this.direction,p),i&&i.copy(ah).addScaledVector(Po,f),m}intersectSphere(e,t){Vi.subVectors(e.center,this.origin);let n=Vi.dot(this.direction),i=Vi.dot(Vi)-n*n,s=e.radius*e.radius;if(i>s)return null;let a=Math.sqrt(s-i),o=n-a,c=n+a;return c<0?null:o<0?this.at(c,t):this.at(o,t)}intersectsSphere(e){return!(e.radius<0)&&this.distanceSqToPoint(e.center)<=e.radius*e.radius}distanceToPlane(e){let t=e.normal.dot(this.direction);if(t===0)return e.distanceToPoint(this.origin)===0?0:null;let n=-(this.origin.dot(e.normal)+e.constant)/t;return n>=0?n:null}intersectPlane(e,t){let n=this.distanceToPlane(e);return n===null?null:this.at(n,t)}intersectsPlane(e){let t=e.distanceToPoint(this.origin);return t===0?!0:e.normal.dot(this.direction)*t<0}intersectBox(e,t){let n,i,s,a,o,c,l=1/this.direction.x,h=1/this.direction.y,p=1/this.direction.z,f=this.origin;return l>=0?(n=(e.min.x-f.x)*l,i=(e.max.x-f.x)*l):(n=(e.max.x-f.x)*l,i=(e.min.x-f.x)*l),h>=0?(s=(e.min.y-f.y)*h,a=(e.max.y-f.y)*h):(s=(e.max.y-f.y)*h,a=(e.min.y-f.y)*h),n>a||s>i?null:((s>n||isNaN(n))&&(n=s),(a<i||isNaN(i))&&(i=a),p>=0?(o=(e.min.z-f.z)*p,c=(e.max.z-f.z)*p):(o=(e.max.z-f.z)*p,c=(e.min.z-f.z)*p),n>c||o>i?null:((o>n||n!=n)&&(n=o),(c<i||i!=i)&&(i=c),i<0?null:this.at(n>=0?n:i,t)))}intersectsBox(e){return this.intersectBox(e,Vi)!==null}intersectTriangle(e,t,n,i,s){oh.subVectors(t,e),Io.subVectors(n,e),lh.crossVectors(oh,Io);let a,o=this.direction.dot(lh);if(o>0){if(i)return null;a=1}else{if(!(o<0))return null;a=-1,o=-o}nr.subVectors(this.origin,e);let c=a*this.direction.dot(Io.crossVectors(nr,Io));if(c<0)return null;let l=a*this.direction.dot(oh.cross(nr));if(l<0||c+l>o)return null;let h=-a*nr.dot(lh);return h<0?null:this.at(h/o,s)}applyMatrix4(e){return this.origin.applyMatrix4(e),this.direction.transformDirection(e),this}equals(e){return e.origin.equals(this.origin)&&e.direction.equals(this.direction)}clone(){return new this.constructor().copy(this)}},cn=class extends Si{constructor(e){super(),this.isMeshBasicMaterial=!0,this.type="MeshBasicMaterial",this.color=new ke(16777215),this.map=null,this.lightMap=null,this.lightMapIntensity=1,this.aoMap=null,this.aoMapIntensity=1,this.specularMap=null,this.alphaMap=null,this.envMap=null,this.envMapRotation=new Wn,this.combine=0,this.reflectivity=1,this.refractionRatio=.98,this.wireframe=!1,this.wireframeLinewidth=1,this.wireframeLinecap="round",this.wireframeLinejoin="round",this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.color.copy(e.color),this.map=e.map,this.lightMap=e.lightMap,this.lightMapIntensity=e.lightMapIntensity,this.aoMap=e.aoMap,this.aoMapIntensity=e.aoMapIntensity,this.specularMap=e.specularMap,this.alphaMap=e.alphaMap,this.envMap=e.envMap,this.envMapRotation.copy(e.envMapRotation),this.combine=e.combine,this.reflectivity=e.reflectivity,this.refractionRatio=e.refractionRatio,this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this.wireframeLinecap=e.wireframeLinecap,this.wireframeLinejoin=e.wireframeLinejoin,this.fog=e.fog,this}},Gd=new st,wr=new Ti,Lo=new ii,Vd=new N,Do=new N,Uo=new N,No=new N,ch=new N,Oo=new N,Wd=new N,Fo=new N,Et=class extends Gt{constructor(e=new St,t=new cn){super(),this.isMesh=!0,this.type="Mesh",this.geometry=e,this.material=t,this.morphTargetDictionary=void 0,this.morphTargetInfluences=void 0,this.count=1,this.updateMorphTargets()}copy(e,t){return super.copy(e,t),e.morphTargetInfluences!==void 0&&(this.morphTargetInfluences=e.morphTargetInfluences.slice()),e.morphTargetDictionary!==void 0&&(this.morphTargetDictionary=Object.assign({},e.morphTargetDictionary)),this.material=Array.isArray(e.material)?e.material.slice():e.material,this.geometry=e.geometry,this}updateMorphTargets(){let e=this.geometry.morphAttributes,t=Object.keys(e);if(t.length>0){let n=e[t[0]];if(n!==void 0){this.morphTargetInfluences=[],this.morphTargetDictionary={};for(let i=0,s=n.length;i<s;i++){let a=n[i].name||String(i);this.morphTargetInfluences.push(0),this.morphTargetDictionary[a]=i}}}}getVertexPosition(e,t){let n=this.geometry,i=n.attributes.position,s=n.morphAttributes.position,a=n.morphTargetsRelative;t.fromBufferAttribute(i,e);let o=this.morphTargetInfluences;if(s&&o){Oo.set(0,0,0);for(let c=0,l=s.length;c<l;c++){let h=o[c],p=s[c];h!==0&&(ch.fromBufferAttribute(p,e),a?Oo.addScaledVector(ch,h):Oo.addScaledVector(ch.sub(t),h))}t.add(Oo)}return t}raycast(e,t){let n=this.geometry,i=this.material,s=this.matrixWorld;if(i!==void 0){if(n.boundingSphere===null&&n.computeBoundingSphere(),Lo.copy(n.boundingSphere),Lo.applyMatrix4(s),wr.copy(e.ray).recast(e.near),Lo.containsPoint(wr.origin)===!1&&(wr.intersectSphere(Lo,Vd)===null||wr.origin.distanceToSquared(Vd)>(e.far-e.near)**2))return;Gd.copy(s).invert(),wr.copy(e.ray).applyMatrix4(Gd),n.boundingBox!==null&&wr.intersectsBox(n.boundingBox)===!1||this._computeIntersections(e,t,wr)}}_computeIntersections(e,t,n){let i,s=this.geometry,a=this.material,o=s.index,c=s.attributes.position,l=s.attributes.uv,h=s.attributes.uv1,p=s.attributes.normal,f=s.groups,m=s.drawRange;if(o!==null)if(Array.isArray(a))for(let v=0,g=f.length;v<g;v++){let u=f[v],y=a[u.materialIndex];for(let x=Math.max(u.start,m.start),S=Math.min(o.count,Math.min(u.start+u.count,m.start+m.count));x<S;x+=3)i=Bo(this,y,e,n,l,h,p,o.getX(x),o.getX(x+1),o.getX(x+2)),i&&(i.faceIndex=Math.floor(x/3),i.face.materialIndex=u.materialIndex,t.push(i))}else for(let v=Math.max(0,m.start),g=Math.min(o.count,m.start+m.count);v<g;v+=3)i=Bo(this,a,e,n,l,h,p,o.getX(v),o.getX(v+1),o.getX(v+2)),i&&(i.faceIndex=Math.floor(v/3),t.push(i));else if(c!==void 0)if(Array.isArray(a))for(let v=0,g=f.length;v<g;v++){let u=f[v],y=a[u.materialIndex];for(let x=Math.max(u.start,m.start),S=Math.min(c.count,Math.min(u.start+u.count,m.start+m.count));x<S;x+=3)i=Bo(this,y,e,n,l,h,p,x,x+1,x+2),i&&(i.faceIndex=Math.floor(x/3),i.face.materialIndex=u.materialIndex,t.push(i))}else for(let v=Math.max(0,m.start),g=Math.min(c.count,m.start+m.count);v<g;v+=3)i=Bo(this,a,e,n,l,h,p,v,v+1,v+2),i&&(i.faceIndex=Math.floor(v/3),t.push(i))}};function Bo(r,e,t,n,i,s,a,o,c,l){r.getVertexPosition(o,Do),r.getVertexPosition(c,Uo),r.getVertexPosition(l,No);let h=function(p,f,m,v,g,u,y,x){let S;if(S=f.side===1?v.intersectTriangle(y,u,g,!0,x):v.intersectTriangle(g,u,y,f.side===0,x),S===null)return null;Fo.copy(x),Fo.applyMatrix4(p.matrixWorld);let E=m.ray.origin.distanceTo(Fo);return E<m.near||E>m.far?null:{distance:E,point:Fo.clone(),object:p}}(r,e,t,n,Do,Uo,No,Wd);if(h){let p=new N;Wi.getBarycoord(Wd,Do,Uo,No,p),i&&(h.uv=Wi.getInterpolatedAttribute(i,o,c,l,p,new Te)),s&&(h.uv1=Wi.getInterpolatedAttribute(s,o,c,l,p,new Te)),a&&(h.normal=Wi.getInterpolatedAttribute(a,o,c,l,p,new N),h.normal.dot(n.direction)>0&&h.normal.multiplyScalar(-1));let f={a:o,b:c,c:l,normal:new N,materialIndex:0};Wi.getNormal(Do,Uo,No,f.normal),h.face=f,h.barycoord=p}return h}var i1=new N,r1=new Lt,s1=new Lt,a1=new N,o1=new st,l1=new N,c1=new ii,h1=new st,u1=new Ti;var Ea=class extends _n{constructor(e=null,t=1,n=1,i,s,a,o,c,l=1003,h=1003,p,f){super(null,a,o,c,l,h,i,s,p,f),this.isDataTexture=!0,this.image={data:e,width:t,height:n},this.generateMipmaps=!1,this.flipY=!1,this.unpackAlignment=1}},d1=new st,p1=new st;var wa=class extends on{constructor(e,t,n,i=1){super(e,t,n),this.isInstancedBufferAttribute=!0,this.meshPerAttribute=i}copy(e){return super.copy(e),this.meshPerAttribute=e.meshPerAttribute,this}toJSON(){let e=super.toJSON();return e.meshPerAttribute=this.meshPerAttribute,e.isInstancedBufferAttribute=!0,e}},cs=new st,jd=new st,zo=[],Xd=new ln,Em=new st,ha=new Et,ua=new ii,Tn=class extends Et{constructor(e,t,n){super(e,t),this.isInstancedMesh=!0,this.instanceMatrix=new wa(new Float32Array(16*n),16),this.previousInstanceMatrix=null,this.instanceColor=null,this.morphTexture=null,this.count=n,this.boundingBox=null,this.boundingSphere=null;for(let i=0;i<n;i++)this.setMatrixAt(i,Em)}computeBoundingBox(){let e=this.geometry,t=this.count;this.boundingBox===null&&(this.boundingBox=new ln),e.boundingBox===null&&e.computeBoundingBox(),this.boundingBox.makeEmpty();for(let n=0;n<t;n++)this.getMatrixAt(n,cs),Xd.copy(e.boundingBox).applyMatrix4(cs),this.boundingBox.union(Xd)}computeBoundingSphere(){let e=this.geometry,t=this.count;this.boundingSphere===null&&(this.boundingSphere=new ii),e.boundingSphere===null&&e.computeBoundingSphere(),this.boundingSphere.makeEmpty();for(let n=0;n<t;n++)this.getMatrixAt(n,cs),ua.copy(e.boundingSphere).applyMatrix4(cs),this.boundingSphere.union(ua)}copy(e,t){return super.copy(e,t),this.instanceMatrix.copy(e.instanceMatrix),e.previousInstanceMatrix!==null&&(this.previousInstanceMatrix=e.previousInstanceMatrix.clone()),e.morphTexture!==null&&(this.morphTexture=e.morphTexture.clone()),e.instanceColor!==null&&(this.instanceColor=e.instanceColor.clone()),this.count=e.count,e.boundingBox!==null&&(this.boundingBox=e.boundingBox.clone()),e.boundingSphere!==null&&(this.boundingSphere=e.boundingSphere.clone()),this}getColorAt(e,t){t.fromArray(this.instanceColor.array,3*e)}getMatrixAt(e,t){t.fromArray(this.instanceMatrix.array,16*e)}getMorphAt(e,t){let n=t.morphTargetInfluences,i=this.morphTexture.source.data.data,s=e*(n.length+1)+1;for(let a=0;a<n.length;a++)n[a]=i[s+a]}raycast(e,t){let n=this.matrixWorld,i=this.count;if(ha.geometry=this.geometry,ha.material=this.material,ha.material!==void 0&&(this.boundingSphere===null&&this.computeBoundingSphere(),ua.copy(this.boundingSphere),ua.applyMatrix4(n),e.ray.intersectsSphere(ua)!==!1))for(let s=0;s<i;s++){this.getMatrixAt(s,cs),jd.multiplyMatrices(n,cs),ha.matrixWorld=jd,ha.raycast(e,zo);for(let a=0,o=zo.length;a<o;a++){let c=zo[a];c.instanceId=s,c.object=this,t.push(c)}zo.length=0}}setColorAt(e,t){this.instanceColor===null&&(this.instanceColor=new wa(new Float32Array(3*this.instanceMatrix.count).fill(1),3)),t.toArray(this.instanceColor.array,3*e)}setMatrixAt(e,t){t.toArray(this.instanceMatrix.array,16*e)}setMorphAt(e,t){let n=t.morphTargetInfluences,i=n.length+1;this.morphTexture===null&&(this.morphTexture=new Ea(new Float32Array(i*this.count),i,this.count,rc,si));let s=this.morphTexture.source.data.data,a=0;for(let l=0;l<n.length;l++)a+=n[l];let o=this.geometry.morphTargetsRelative?1:1-a,c=i*e;s[c]=o,s.set(n,c+1)}updateMorphTargets(){}dispose(){this.dispatchEvent({type:"dispose"}),this.morphTexture!==null&&(this.morphTexture.dispose(),this.morphTexture=null)}},hh=new N,wm=new N,Am=new ht,sn=class{constructor(e=new N(1,0,0),t=0){this.isPlane=!0,this.normal=e,this.constant=t}set(e,t){return this.normal.copy(e),this.constant=t,this}setComponents(e,t,n,i){return this.normal.set(e,t,n),this.constant=i,this}setFromNormalAndCoplanarPoint(e,t){return this.normal.copy(e),this.constant=-t.dot(this.normal),this}setFromCoplanarPoints(e,t,n){let i=hh.subVectors(n,t).cross(wm.subVectors(e,t)).normalize();return this.setFromNormalAndCoplanarPoint(i,e),this}copy(e){return this.normal.copy(e.normal),this.constant=e.constant,this}normalize(){let e=1/this.normal.length();return this.normal.multiplyScalar(e),this.constant*=e,this}negate(){return this.constant*=-1,this.normal.negate(),this}distanceToPoint(e){return this.normal.dot(e)+this.constant}distanceToSphere(e){return this.distanceToPoint(e.center)-e.radius}projectPoint(e,t){return t.copy(e).addScaledVector(this.normal,-this.distanceToPoint(e))}intersectLine(e,t){let n=e.delta(hh),i=this.normal.dot(n);if(i===0)return this.distanceToPoint(e.start)===0?t.copy(e.start):null;let s=-(e.start.dot(this.normal)+this.constant)/i;return s<0||s>1?null:t.copy(e.start).addScaledVector(n,s)}intersectsLine(e){let t=this.distanceToPoint(e.start),n=this.distanceToPoint(e.end);return t<0&&n>0||n<0&&t>0}intersectsBox(e){return e.intersectsPlane(this)}intersectsSphere(e){return e.intersectsPlane(this)}coplanarPoint(e){return e.copy(this.normal).multiplyScalar(-this.constant)}applyMatrix4(e,t){let n=t||Am.getNormalMatrix(e),i=this.coplanarPoint(hh).applyMatrix4(e),s=this.normal.applyMatrix3(n).normalize();return this.constant=-i.dot(s),this}translate(e){return this.constant-=e.dot(this.normal),this}equals(e){return e.normal.equals(this.normal)&&e.constant===this.constant}clone(){return new this.constructor().copy(this)}},Ar=new ii,Cm=new Te(.5,.5),ko=new N,or=class{constructor(e=new sn,t=new sn,n=new sn,i=new sn,s=new sn,a=new sn){this.planes=[e,t,n,i,s,a]}set(e,t,n,i,s,a){let o=this.planes;return o[0].copy(e),o[1].copy(t),o[2].copy(n),o[3].copy(i),o[4].copy(s),o[5].copy(a),this}copy(e){let t=this.planes;for(let n=0;n<6;n++)t[n].copy(e.planes[n]);return this}setFromProjectionMatrix(e,t=2e3,n=!1){let i=this.planes,s=e.elements,a=s[0],o=s[1],c=s[2],l=s[3],h=s[4],p=s[5],f=s[6],m=s[7],v=s[8],g=s[9],u=s[10],y=s[11],x=s[12],S=s[13],E=s[14],R=s[15];if(i[0].setComponents(l-a,m-h,y-v,R-x).normalize(),i[1].setComponents(l+a,m+h,y+v,R+x).normalize(),i[2].setComponents(l+o,m+p,y+g,R+S).normalize(),i[3].setComponents(l-o,m-p,y-g,R-S).normalize(),n)i[4].setComponents(c,f,u,E).normalize(),i[5].setComponents(l-c,m-f,y-u,R-E).normalize();else if(i[4].setComponents(l-c,m-f,y-u,R-E).normalize(),t===Xi)i[5].setComponents(l+c,m+f,y+u,R+E).normalize();else{if(t!==gs)throw new Error("THREE.Frustum.setFromProjectionMatrix(): Invalid coordinate system: "+t);i[5].setComponents(c,f,u,E).normalize()}return this}intersectsObject(e){if(e.boundingSphere!==void 0)e.boundingSphere===null&&e.computeBoundingSphere(),Ar.copy(e.boundingSphere).applyMatrix4(e.matrixWorld);else{let t=e.geometry;t.boundingSphere===null&&t.computeBoundingSphere(),Ar.copy(t.boundingSphere).applyMatrix4(e.matrixWorld)}return this.intersectsSphere(Ar)}intersectsSprite(e){Ar.center.set(0,0,0);let t=Cm.distanceTo(e.center);return Ar.radius=.7071067811865476+t,Ar.applyMatrix4(e.matrixWorld),this.intersectsSphere(Ar)}intersectsSphere(e){let t=this.planes,n=e.center,i=-e.radius;for(let s=0;s<6;s++)if(t[s].distanceToPoint(n)<i)return!1;return!0}intersectsBox(e){let t=this.planes;for(let n=0;n<6;n++){let i=t[n];if(ko.x=i.normal.x>0?e.max.x:e.min.x,ko.y=i.normal.y>0?e.max.y:e.min.y,ko.z=i.normal.z>0?e.max.z:e.min.z,i.distanceToPoint(ko)<0)return!1}return!0}containsPoint(e){let t=this.planes;for(let n=0;n<6;n++)if(t[n].distanceToPoint(e)<0)return!1;return!0}clone(){return new this.constructor().copy(this)}},vi=new st,yi=new or,ll=class r{constructor(){this.coordinateSystem=Xi}intersectsObject(e,t){if(!t.isArrayCamera||t.cameras.length===0)return!1;for(let n=0;n<t.cameras.length;n++){let i=t.cameras[n];if(vi.multiplyMatrices(i.projectionMatrix,i.matrixWorldInverse),yi.setFromProjectionMatrix(vi,i.coordinateSystem,i.reversedDepth),yi.intersectsObject(e))return!0}return!1}intersectsSprite(e,t){if(!t||!t.cameras||t.cameras.length===0)return!1;for(let n=0;n<t.cameras.length;n++){let i=t.cameras[n];if(vi.multiplyMatrices(i.projectionMatrix,i.matrixWorldInverse),yi.setFromProjectionMatrix(vi,i.coordinateSystem,i.reversedDepth),yi.intersectsSprite(e))return!0}return!1}intersectsSphere(e,t){if(!t||!t.cameras||t.cameras.length===0)return!1;for(let n=0;n<t.cameras.length;n++){let i=t.cameras[n];if(vi.multiplyMatrices(i.projectionMatrix,i.matrixWorldInverse),yi.setFromProjectionMatrix(vi,i.coordinateSystem,i.reversedDepth),yi.intersectsSphere(e))return!0}return!1}intersectsBox(e,t){if(!t||!t.cameras||t.cameras.length===0)return!1;for(let n=0;n<t.cameras.length;n++){let i=t.cameras[n];if(vi.multiplyMatrices(i.projectionMatrix,i.matrixWorldInverse),yi.setFromProjectionMatrix(vi,i.coordinateSystem,i.reversedDepth),yi.intersectsBox(e))return!0}return!1}containsPoint(e,t){if(!t||!t.cameras||t.cameras.length===0)return!1;for(let n=0;n<t.cameras.length;n++){let i=t.cameras[n];if(vi.multiplyMatrices(i.projectionMatrix,i.matrixWorldInverse),yi.setFromProjectionMatrix(vi,i.coordinateSystem,i.reversedDepth),yi.containsPoint(e))return!0}return!1}clone(){return new r}};var Th=class{constructor(){this.index=0,this.pool=[],this.list=[]}push(e,t,n,i){let s=this.pool,a=this.list;this.index>=s.length&&s.push({start:-1,count:-1,z:-1,index:-1});let o=s[this.index];a.push(o),this.index++,o.start=e,o.count=t,o.z=n,o.index=i}reset(){this.list.length=0,this.index=0}},f1=new st,m1=new ke(1,1,1),g1=new or,_1=new ll,v1=new ln,y1=new ii,x1=new N,M1=new N,b1=new N,S1=new Th,T1=new Et;var Ss=class extends Si{constructor(e){super(),this.isLineBasicMaterial=!0,this.type="LineBasicMaterial",this.color=new ke(16777215),this.map=null,this.linewidth=1,this.linecap="round",this.linejoin="round",this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.color.copy(e.color),this.map=e.map,this.linewidth=e.linewidth,this.linecap=e.linecap,this.linejoin=e.linejoin,this.fog=e.fog,this}},cl=new N,hl=new N,qd=new st,da=new Ti,Ho=new ii,uh=new N,Yd=new N,ul=class extends Gt{constructor(e=new St,t=new Ss){super(),this.isLine=!0,this.type="Line",this.geometry=e,this.material=t,this.morphTargetDictionary=void 0,this.morphTargetInfluences=void 0,this.updateMorphTargets()}copy(e,t){return super.copy(e,t),this.material=Array.isArray(e.material)?e.material.slice():e.material,this.geometry=e.geometry,this}computeLineDistances(){let e=this.geometry;if(e.index===null){let t=e.attributes.position,n=[0];for(let i=1,s=t.count;i<s;i++)cl.fromBufferAttribute(t,i-1),hl.fromBufferAttribute(t,i),n[i]=n[i-1],n[i]+=cl.distanceTo(hl);e.setAttribute("lineDistance",new $e(n,1))}else tt("Line.computeLineDistances(): Computation only possible with non-indexed BufferGeometry.");return this}raycast(e,t){let n=this.geometry,i=this.matrixWorld,s=e.params.Line.threshold,a=n.drawRange;if(n.boundingSphere===null&&n.computeBoundingSphere(),Ho.copy(n.boundingSphere),Ho.applyMatrix4(i),Ho.radius+=s,e.ray.intersectsSphere(Ho)===!1)return;qd.copy(i).invert(),da.copy(e.ray).applyMatrix4(qd);let o=s/((this.scale.x+this.scale.y+this.scale.z)/3),c=o*o,l=this.isLineSegments?2:1,h=n.index,p=n.attributes.position;if(h!==null){let f=Math.max(0,a.start),m=Math.min(h.count,a.start+a.count);for(let v=f,g=m-1;v<g;v+=l){let u=h.getX(v),y=h.getX(v+1),x=Go(this,e,da,c,u,y,v);x&&t.push(x)}if(this.isLineLoop){let v=h.getX(m-1),g=h.getX(f),u=Go(this,e,da,c,v,g,m-1);u&&t.push(u)}}else{let f=Math.max(0,a.start),m=Math.min(p.count,a.start+a.count);for(let v=f,g=m-1;v<g;v+=l){let u=Go(this,e,da,c,v,v+1,v);u&&t.push(u)}if(this.isLineLoop){let v=Go(this,e,da,c,m-1,f,m-1);v&&t.push(v)}}}updateMorphTargets(){let e=this.geometry.morphAttributes,t=Object.keys(e);if(t.length>0){let n=e[t[0]];if(n!==void 0){this.morphTargetInfluences=[],this.morphTargetDictionary={};for(let i=0,s=n.length;i<s;i++){let a=n[i].name||String(i);this.morphTargetInfluences.push(0),this.morphTargetDictionary[a]=i}}}}};function Go(r,e,t,n,i,s,a){let o=r.geometry.attributes.position;if(cl.fromBufferAttribute(o,i),hl.fromBufferAttribute(o,s),t.distanceSqToSegment(cl,hl,uh,Yd)>n)return;uh.applyMatrix4(r.matrixWorld);let c=e.ray.origin.distanceTo(uh);return c<e.near||c>e.far?void 0:{distance:c,point:Yd.clone().applyMatrix4(r.matrixWorld),index:a,face:null,faceIndex:null,barycoord:null,object:r}}var Zd=new N,Jd=new N,Aa=class extends ul{constructor(e,t){super(e,t),this.isLineSegments=!0,this.type="LineSegments"}computeLineDistances(){let e=this.geometry;if(e.index===null){let t=e.attributes.position,n=[];for(let i=0,s=t.count;i<s;i+=2)Zd.fromBufferAttribute(t,i),Jd.fromBufferAttribute(t,i+1),n[i]=i===0?0:n[i-1],n[i+1]=n[i]+Zd.distanceTo(Jd);e.setAttribute("lineDistance",new $e(n,1))}else tt("LineSegments.computeLineDistances(): Computation only possible with non-indexed BufferGeometry.");return this}};var Ts=class extends Si{constructor(e){super(),this.isPointsMaterial=!0,this.type="PointsMaterial",this.color=new ke(16777215),this.map=null,this.alphaMap=null,this.size=1,this.sizeAttenuation=!0,this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.color.copy(e.color),this.map=e.map,this.alphaMap=e.alphaMap,this.size=e.size,this.sizeAttenuation=e.sizeAttenuation,this.fog=e.fog,this}},Kd=new st,Eh=new Ti,Vo=new ii,Wo=new N,Ca=class extends Gt{constructor(e=new St,t=new Ts){super(),this.isPoints=!0,this.type="Points",this.geometry=e,this.material=t,this.morphTargetDictionary=void 0,this.morphTargetInfluences=void 0,this.updateMorphTargets()}copy(e,t){return super.copy(e,t),this.material=Array.isArray(e.material)?e.material.slice():e.material,this.geometry=e.geometry,this}raycast(e,t){let n=this.geometry,i=this.matrixWorld,s=e.params.Points.threshold,a=n.drawRange;if(n.boundingSphere===null&&n.computeBoundingSphere(),Vo.copy(n.boundingSphere),Vo.applyMatrix4(i),Vo.radius+=s,e.ray.intersectsSphere(Vo)===!1)return;Kd.copy(i).invert(),Eh.copy(e.ray).applyMatrix4(Kd);let o=s/((this.scale.x+this.scale.y+this.scale.z)/3),c=o*o,l=n.index,h=n.attributes.position;if(l!==null)for(let p=Math.max(0,a.start),f=Math.min(l.count,a.start+a.count);p<f;p++){let m=l.getX(p);Wo.fromBufferAttribute(h,m),$d(Wo,m,c,i,e,t,this)}else for(let p=Math.max(0,a.start),f=Math.min(h.count,a.start+a.count);p<f;p++)Wo.fromBufferAttribute(h,p),$d(Wo,p,c,i,e,t,this)}updateMorphTargets(){let e=this.geometry.morphAttributes,t=Object.keys(e);if(t.length>0){let n=e[t[0]];if(n!==void 0){this.morphTargetInfluences=[],this.morphTargetDictionary={};for(let i=0,s=n.length;i<s;i++){let a=n[i].name||String(i);this.morphTargetInfluences.push(0),this.morphTargetDictionary[a]=i}}}}};function $d(r,e,t,n,i,s,a){let o=Eh.distanceSqToPoint(r);if(o<t){let c=new N;Eh.closestPointToPoint(r,c),c.applyMatrix4(n);let l=i.ray.origin.distanceTo(c);if(l<i.near||l>i.far)return;s.push({distance:l,distanceToRay:Math.sqrt(o),point:c,index:e,face:null,faceIndex:null,barycoord:null,object:a})}}var Ra=class extends _n{constructor(e=[],t=301,n,i,s,a,o,c,l,h){super(e,t,n,i,s,a,o,c,l,h),this.isCubeTexture=!0,this.flipY=!1}get images(){return this.image}set images(e){this.image=e}},ri=class extends _n{constructor(e,t,n,i,s,a,o,c,l){super(e,t,n,i,s,a,o,c,l),this.isCanvasTexture=!0,this.needsUpdate=!0}},lr=class extends _n{constructor(e,t,n=1014,i,s,a,o=1003,c=1003,l,h=1026,p=1){if(h!==pr&&h!==1027)throw new Error("DepthTexture format must be either THREE.DepthFormat or THREE.DepthStencilFormat");super({width:e,height:t,depth:p},i,s,a,o,c,h,n,l),this.isDepthTexture=!0,this.flipY=!1,this.generateMipmaps=!1,this.compareFunction=null}copy(e){return super.copy(e),this.source=new xs(Object.assign({},e.image)),this.compareFunction=e.compareFunction,this}toJSON(e){let t=super.toJSON(e);return this.compareFunction!==null&&(t.compareFunction=this.compareFunction),t}},dl=class extends lr{constructor(e,t=1014,n=301,i,s,a=1003,o=1003,c,l=1026){let h={width:e,height:e,depth:1},p=[h,h,h,h,h,h];super(e,e,t,n,i,s,a,o,c,l),this.image=p,this.isCubeDepthTexture=!0,this.isCubeTexture=!0}get images(){return this.image}set images(e){this.image=e}},Pa=class extends _n{constructor(e=null){super(),this.sourceTexture=e,this.isExternalTexture=!0}copy(e){return super.copy(e),this.sourceTexture=e.sourceTexture,this}},jn=class r extends St{constructor(e=1,t=1,n=1,i=1,s=1,a=1){super(),this.type="BoxGeometry",this.parameters={width:e,height:t,depth:n,widthSegments:i,heightSegments:s,depthSegments:a};let o=this;i=Math.floor(i),s=Math.floor(s),a=Math.floor(a);let c=[],l=[],h=[],p=[],f=0,m=0;function v(g,u,y,x,S,E,R,P,V,X,H){let se=E/V,oe=R/X,te=E/2,ne=R/2,j=P/2,J=V+1,ie=X+1,re=0,Se=0,we=new N;for(let ze=0;ze<ie;ze++){let Ae=ze*oe-ne;for(let Ie=0;Ie<J;Ie++){let ve=Ie*se-te;we[g]=ve*x,we[u]=Ae*S,we[y]=j,l.push(we.x,we.y,we.z),we[g]=0,we[u]=0,we[y]=P>0?1:-1,h.push(we.x,we.y,we.z),p.push(Ie/V),p.push(1-ze/X),re+=1}}for(let ze=0;ze<X;ze++)for(let Ae=0;Ae<V;Ae++){let Ie=f+Ae+J*ze,ve=f+Ae+J*(ze+1),Ee=f+(Ae+1)+J*(ze+1),Le=f+(Ae+1)+J*ze;c.push(Ie,ve,Le),c.push(ve,Ee,Le),Se+=6}o.addGroup(m,Se,H),m+=Se,f+=re}v("z","y","x",-1,-1,n,t,e,a,s,0),v("z","y","x",1,-1,n,t,-e,a,s,1),v("x","z","y",1,1,e,n,t,i,a,2),v("x","z","y",1,-1,e,n,-t,i,a,3),v("x","y","z",1,-1,e,t,n,i,s,4),v("x","y","z",-1,-1,e,t,-n,i,s,5),this.setIndex(c),this.setAttribute("position",new $e(l,3)),this.setAttribute("normal",new $e(h,3)),this.setAttribute("uv",new $e(p,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.width,e.height,e.depth,e.widthSegments,e.heightSegments,e.depthSegments)}},pl=class r extends St{constructor(e=1,t=1,n=4,i=8,s=1){super(),this.type="CapsuleGeometry",this.parameters={radius:e,height:t,capSegments:n,radialSegments:i,heightSegments:s},t=Math.max(0,t),n=Math.max(1,Math.floor(n)),i=Math.max(3,Math.floor(i)),s=Math.max(1,Math.floor(s));let a=[],o=[],c=[],l=[],h=t/2,p=Math.PI/2*e,f=t,m=2*p+f,v=2*n+s,g=i+1,u=new N,y=new N;for(let x=0;x<=v;x++){let S=0,E=0,R=0,P=0;if(x<=n){let H=x/n,se=H*Math.PI/2;E=-h-e*Math.cos(se),R=e*Math.sin(se),P=-e*Math.cos(se),S=H*p}else if(x<=n+s){let H=(x-n)/s;E=H*t-h,R=e,P=0,S=p+H*f}else{let H=(x-n-s)/n,se=H*Math.PI/2;E=h+e*Math.sin(se),R=e*Math.cos(se),P=e*Math.sin(se),S=p+f+H*p}let V=Math.max(0,Math.min(1,S/m)),X=0;x===0?X=.5/i:x===v&&(X=-.5/i);for(let H=0;H<=i;H++){let se=H/i,oe=se*Math.PI*2,te=Math.sin(oe),ne=Math.cos(oe);y.x=-R*ne,y.y=E,y.z=R*te,o.push(y.x,y.y,y.z),u.set(-R*ne,P,R*te),u.normalize(),c.push(u.x,u.y,u.z),l.push(se+X,V)}if(x>0){let H=(x-1)*g;for(let se=0;se<i;se++){let oe=H+se,te=H+se+1,ne=x*g+se,j=x*g+se+1;a.push(oe,te,ne),a.push(te,j,ne)}}}this.setIndex(a),this.setAttribute("position",new $e(o,3)),this.setAttribute("normal",new $e(c,3)),this.setAttribute("uv",new $e(l,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radius,e.height,e.capSegments,e.radialSegments,e.heightSegments)}},fl=class r extends St{constructor(e=1,t=32,n=0,i=2*Math.PI){super(),this.type="CircleGeometry",this.parameters={radius:e,segments:t,thetaStart:n,thetaLength:i},t=Math.max(3,t);let s=[],a=[],o=[],c=[],l=new N,h=new Te;a.push(0,0,0),o.push(0,0,1),c.push(.5,.5);for(let p=0,f=3;p<=t;p++,f+=3){let m=n+p/t*i;l.x=e*Math.cos(m),l.y=e*Math.sin(m),a.push(l.x,l.y,l.z),o.push(0,0,1),h.x=(a[f]/e+1)/2,h.y=(a[f+1]/e+1)/2,c.push(h.x,h.y)}for(let p=1;p<=t;p++)s.push(p,p+1,0);this.setIndex(s),this.setAttribute("position",new $e(a,3)),this.setAttribute("normal",new $e(o,3)),this.setAttribute("uv",new $e(c,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radius,e.segments,e.thetaStart,e.thetaLength)}},Ia=class r extends St{constructor(e=1,t=1,n=1,i=32,s=1,a=!1,o=0,c=2*Math.PI){super(),this.type="CylinderGeometry",this.parameters={radiusTop:e,radiusBottom:t,height:n,radialSegments:i,heightSegments:s,openEnded:a,thetaStart:o,thetaLength:c};let l=this;i=Math.floor(i),s=Math.floor(s);let h=[],p=[],f=[],m=[],v=0,g=[],u=n/2,y=0;function x(S){let E=v,R=new Te,P=new N,V=0,X=S===!0?e:t,H=S===!0?1:-1;for(let oe=1;oe<=i;oe++)p.push(0,u*H,0),f.push(0,H,0),m.push(.5,.5),v++;let se=v;for(let oe=0;oe<=i;oe++){let te=oe/i*c+o,ne=Math.cos(te),j=Math.sin(te);P.x=X*j,P.y=u*H,P.z=X*ne,p.push(P.x,P.y,P.z),f.push(0,H,0),R.x=.5*ne+.5,R.y=.5*j*H+.5,m.push(R.x,R.y),v++}for(let oe=0;oe<i;oe++){let te=E+oe,ne=se+oe;S===!0?h.push(ne,ne+1,te):h.push(ne+1,ne,te),V+=3}l.addGroup(y,V,S===!0?1:2),y+=V}(function(){let S=new N,E=new N,R=0,P=(t-e)/n;for(let V=0;V<=s;V++){let X=[],H=V/s,se=H*(t-e)+e;for(let oe=0;oe<=i;oe++){let te=oe/i,ne=te*c+o,j=Math.sin(ne),J=Math.cos(ne);E.x=se*j,E.y=-H*n+u,E.z=se*J,p.push(E.x,E.y,E.z),S.set(j,P,J).normalize(),f.push(S.x,S.y,S.z),m.push(te,1-H),X.push(v++)}g.push(X)}for(let V=0;V<i;V++)for(let X=0;X<s;X++){let H=g[X][V],se=g[X+1][V],oe=g[X+1][V+1],te=g[X][V+1];(e>0||X!==0)&&(h.push(H,se,te),R+=3),(t>0||X!==s-1)&&(h.push(se,oe,te),R+=3)}l.addGroup(y,R,0),y+=R})(),a===!1&&(e>0&&x(!0),t>0&&x(!1)),this.setIndex(h),this.setAttribute("position",new $e(p,3)),this.setAttribute("normal",new $e(f,3)),this.setAttribute("uv",new $e(m,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radiusTop,e.radiusBottom,e.height,e.radialSegments,e.heightSegments,e.openEnded,e.thetaStart,e.thetaLength)}},ml=class r extends Ia{constructor(e=1,t=1,n=32,i=1,s=!1,a=0,o=2*Math.PI){super(0,e,t,n,i,s,a,o),this.type="ConeGeometry",this.parameters={radius:e,height:t,radialSegments:n,heightSegments:i,openEnded:s,thetaStart:a,thetaLength:o}}static fromJSON(e){return new r(e.radius,e.height,e.radialSegments,e.heightSegments,e.openEnded,e.thetaStart,e.thetaLength)}},cr=class r extends St{constructor(e=[],t=[],n=1,i=0){super(),this.type="PolyhedronGeometry",this.parameters={vertices:e,indices:t,radius:n,detail:i};let s=[],a=[];function o(m,v,g,u){let y=u+1,x=[];for(let S=0;S<=y;S++){x[S]=[];let E=m.clone().lerp(g,S/y),R=v.clone().lerp(g,S/y),P=y-S;for(let V=0;V<=P;V++)x[S][V]=V===0&&S===y?E:E.clone().lerp(R,V/P)}for(let S=0;S<y;S++)for(let E=0;E<2*(y-S)-1;E++){let R=Math.floor(E/2);E%2==0?(c(x[S][R+1]),c(x[S+1][R]),c(x[S][R])):(c(x[S][R+1]),c(x[S+1][R+1]),c(x[S+1][R]))}}function c(m){s.push(m.x,m.y,m.z)}function l(m,v){let g=3*m;v.x=e[g+0],v.y=e[g+1],v.z=e[g+2]}function h(m,v,g,u){u<0&&m.x===1&&(a[v]=m.x-1),g.x===0&&g.z===0&&(a[v]=u/2/Math.PI+.5)}function p(m){return Math.atan2(m.z,-m.x)}function f(m){return Math.atan2(-m.y,Math.sqrt(m.x*m.x+m.z*m.z))}(function(m){let v=new N,g=new N,u=new N;for(let y=0;y<t.length;y+=3)l(t[y+0],v),l(t[y+1],g),l(t[y+2],u),o(v,g,u,m)})(i),function(m){let v=new N;for(let g=0;g<s.length;g+=3)v.x=s[g+0],v.y=s[g+1],v.z=s[g+2],v.normalize().multiplyScalar(m),s[g+0]=v.x,s[g+1]=v.y,s[g+2]=v.z}(n),function(){let m=new N;for(let v=0;v<s.length;v+=3){m.x=s[v+0],m.y=s[v+1],m.z=s[v+2];let g=p(m)/2/Math.PI+.5,u=f(m)/Math.PI+.5;a.push(g,1-u)}(function(){let v=new N,g=new N,u=new N,y=new N,x=new Te,S=new Te,E=new Te;for(let R=0,P=0;R<s.length;R+=9,P+=6){v.set(s[R+0],s[R+1],s[R+2]),g.set(s[R+3],s[R+4],s[R+5]),u.set(s[R+6],s[R+7],s[R+8]),x.set(a[P+0],a[P+1]),S.set(a[P+2],a[P+3]),E.set(a[P+4],a[P+5]),y.copy(v).add(g).add(u).divideScalar(3);let V=p(y);h(x,P+0,v,V),h(S,P+2,g,V),h(E,P+4,u,V)}})(),function(){for(let v=0;v<a.length;v+=6){let g=a[v+0],u=a[v+2],y=a[v+4],x=Math.max(g,u,y),S=Math.min(g,u,y);x>.9&&S<.1&&(g<.2&&(a[v+0]+=1),u<.2&&(a[v+2]+=1),y<.2&&(a[v+4]+=1))}}()}(),this.setAttribute("position",new $e(s,3)),this.setAttribute("normal",new $e(s.slice(),3)),this.setAttribute("uv",new $e(a,2)),i===0?this.computeVertexNormals():this.normalizeNormals()}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.vertices,e.indices,e.radius,e.detail)}},gl=class r extends cr{constructor(e=1,t=0){let n=(1+Math.sqrt(5))/2,i=1/n;super([-1,-1,-1,-1,-1,1,-1,1,-1,-1,1,1,1,-1,-1,1,-1,1,1,1,-1,1,1,1,0,-i,-n,0,-i,n,0,i,-n,0,i,n,-i,-n,0,-i,n,0,i,-n,0,i,n,0,-n,0,-i,n,0,-i,-n,0,i,n,0,i],[3,11,7,3,7,15,3,15,13,7,19,17,7,17,6,7,6,15,17,4,8,17,8,10,17,10,6,8,0,16,8,16,2,8,2,10,0,12,1,0,1,18,0,18,16,6,10,2,6,2,13,6,13,15,2,16,18,2,18,3,2,3,13,18,1,9,18,9,11,18,11,3,4,14,12,4,12,0,4,0,8,11,9,5,11,5,19,11,19,7,19,5,14,19,14,4,19,4,17,1,12,14,1,14,5,1,5,9],e,t),this.type="DodecahedronGeometry",this.parameters={radius:e,detail:t}}static fromJSON(e){return new r(e.radius,e.detail)}},jo=new N,Xo=new N,dh=new N,qo=new Wi,_l=class extends St{constructor(e=null,t=1){if(super(),this.type="EdgesGeometry",this.parameters={geometry:e,thresholdAngle:t},e!==null){let i=Math.pow(10,4),s=Math.cos(ps*t),a=e.getIndex(),o=e.getAttribute("position"),c=a?a.count:o.count,l=[0,0,0],h=["a","b","c"],p=new Array(3),f={},m=[];for(let v=0;v<c;v+=3){a?(l[0]=a.getX(v),l[1]=a.getX(v+1),l[2]=a.getX(v+2)):(l[0]=v,l[1]=v+1,l[2]=v+2);let{a:g,b:u,c:y}=qo;if(g.fromBufferAttribute(o,l[0]),u.fromBufferAttribute(o,l[1]),y.fromBufferAttribute(o,l[2]),qo.getNormal(dh),p[0]=`${Math.round(g.x*i)},${Math.round(g.y*i)},${Math.round(g.z*i)}`,p[1]=`${Math.round(u.x*i)},${Math.round(u.y*i)},${Math.round(u.z*i)}`,p[2]=`${Math.round(y.x*i)},${Math.round(y.y*i)},${Math.round(y.z*i)}`,p[0]!==p[1]&&p[1]!==p[2]&&p[2]!==p[0])for(let x=0;x<3;x++){let S=(x+1)%3,E=p[x],R=p[S],P=qo[h[x]],V=qo[h[S]],X=`${E}_${R}`,H=`${R}_${E}`;H in f&&f[H]?(dh.dot(f[H].normal)<=s&&(m.push(P.x,P.y,P.z),m.push(V.x,V.y,V.z)),f[H]=null):X in f||(f[X]={index0:l[x],index1:l[S],normal:dh.clone()})}}for(let v in f)if(f[v]){let{index0:g,index1:u}=f[v];jo.fromBufferAttribute(o,g),Xo.fromBufferAttribute(o,u),m.push(jo.x,jo.y,jo.z),m.push(Xo.x,Xo.y,Xo.z)}this.setAttribute("position",new $e(m,3))}}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}},Xn=class{constructor(){this.type="Curve",this.arcLengthDivisions=200,this.needsUpdate=!1,this.cacheArcLengths=null}getPoint(){tt("Curve: .getPoint() not implemented.")}getPointAt(e,t){let n=this.getUtoTmapping(e);return this.getPoint(n,t)}getPoints(e=5){let t=[];for(let n=0;n<=e;n++)t.push(this.getPoint(n/e));return t}getSpacedPoints(e=5){let t=[];for(let n=0;n<=e;n++)t.push(this.getPointAt(n/e));return t}getLength(){let e=this.getLengths();return e[e.length-1]}getLengths(e=this.arcLengthDivisions){if(this.cacheArcLengths&&this.cacheArcLengths.length===e+1&&!this.needsUpdate)return this.cacheArcLengths;this.needsUpdate=!1;let t=[],n,i=this.getPoint(0),s=0;t.push(0);for(let a=1;a<=e;a++)n=this.getPoint(a/e),s+=n.distanceTo(i),t.push(s),i=n;return this.cacheArcLengths=t,t}updateArcLengths(){this.needsUpdate=!0,this.getLengths()}getUtoTmapping(e,t=null){let n=this.getLengths(),i=0,s=n.length,a;a=t||e*n[s-1];let o,c=0,l=s-1;for(;c<=l;)if(i=Math.floor(c+(l-c)/2),o=n[i]-a,o<0)c=i+1;else{if(!(o>0)){l=i;break}l=i-1}if(i=l,n[i]===a)return i/(s-1);let h=n[i];return(i+(a-h)/(n[i+1]-h))/(s-1)}getTangent(e,t){let i=e-1e-4,s=e+1e-4;i<0&&(i=0),s>1&&(s=1);let a=this.getPoint(i),o=this.getPoint(s),c=t||(a.isVector2?new Te:new N);return c.copy(o).sub(a).normalize(),c}getTangentAt(e,t){let n=this.getUtoTmapping(e);return this.getTangent(n,t)}computeFrenetFrames(e,t=!1){let n=new N,i=[],s=[],a=[],o=new N,c=new st;for(let m=0;m<=e;m++){let v=m/e;i[m]=this.getTangentAt(v,new N)}s[0]=new N,a[0]=new N;let l=Number.MAX_VALUE,h=Math.abs(i[0].x),p=Math.abs(i[0].y),f=Math.abs(i[0].z);h<=l&&(l=h,n.set(1,0,0)),p<=l&&(l=p,n.set(0,1,0)),f<=l&&n.set(0,0,1),o.crossVectors(i[0],n).normalize(),s[0].crossVectors(i[0],o),a[0].crossVectors(i[0],s[0]);for(let m=1;m<=e;m++){if(s[m]=s[m-1].clone(),a[m]=a[m-1].clone(),o.crossVectors(i[m-1],i[m]),o.length()>Number.EPSILON){o.normalize();let v=Math.acos(ut(i[m-1].dot(i[m]),-1,1));s[m].applyMatrix4(c.makeRotationAxis(o,v))}a[m].crossVectors(i[m],s[m])}if(t===!0){let m=Math.acos(ut(s[0].dot(s[e]),-1,1));m/=e,i[0].dot(o.crossVectors(s[0],s[e]))>0&&(m=-m);for(let v=1;v<=e;v++)s[v].applyMatrix4(c.makeRotationAxis(i[v],m*v)),a[v].crossVectors(i[v],s[v])}return{tangents:i,normals:s,binormals:a}}clone(){return new this.constructor().copy(this)}copy(e){return this.arcLengthDivisions=e.arcLengthDivisions,this}toJSON(){let e={metadata:{version:4.7,type:"Curve",generator:"Curve.toJSON"}};return e.arcLengthDivisions=this.arcLengthDivisions,e.type=this.type,e}fromJSON(e){return this.arcLengthDivisions=e.arcLengthDivisions,this}},Es=class extends Xn{constructor(e=0,t=0,n=1,i=1,s=0,a=2*Math.PI,o=!1,c=0){super(),this.isEllipseCurve=!0,this.type="EllipseCurve",this.aX=e,this.aY=t,this.xRadius=n,this.yRadius=i,this.aStartAngle=s,this.aEndAngle=a,this.aClockwise=o,this.aRotation=c}getPoint(e,t=new Te){let n=t,i=2*Math.PI,s=this.aEndAngle-this.aStartAngle,a=Math.abs(s)<Number.EPSILON;for(;s<0;)s+=i;for(;s>i;)s-=i;s<Number.EPSILON&&(s=a?0:i),this.aClockwise!==!0||a||(s===i?s=-i:s-=i);let o=this.aStartAngle+e*s,c=this.aX+this.xRadius*Math.cos(o),l=this.aY+this.yRadius*Math.sin(o);if(this.aRotation!==0){let h=Math.cos(this.aRotation),p=Math.sin(this.aRotation),f=c-this.aX,m=l-this.aY;c=f*h-m*p+this.aX,l=f*p+m*h+this.aY}return n.set(c,l)}copy(e){return super.copy(e),this.aX=e.aX,this.aY=e.aY,this.xRadius=e.xRadius,this.yRadius=e.yRadius,this.aStartAngle=e.aStartAngle,this.aEndAngle=e.aEndAngle,this.aClockwise=e.aClockwise,this.aRotation=e.aRotation,this}toJSON(){let e=super.toJSON();return e.aX=this.aX,e.aY=this.aY,e.xRadius=this.xRadius,e.yRadius=this.yRadius,e.aStartAngle=this.aStartAngle,e.aEndAngle=this.aEndAngle,e.aClockwise=this.aClockwise,e.aRotation=this.aRotation,e}fromJSON(e){return super.fromJSON(e),this.aX=e.aX,this.aY=e.aY,this.xRadius=e.xRadius,this.yRadius=e.yRadius,this.aStartAngle=e.aStartAngle,this.aEndAngle=e.aEndAngle,this.aClockwise=e.aClockwise,this.aRotation=e.aRotation,this}},vl=class extends Es{constructor(e,t,n,i,s,a){super(e,t,n,n,i,s,a),this.isArcCurve=!0,this.type="ArcCurve"}};function Nu(){let r=0,e=0,t=0,n=0;function i(s,a,o,c){r=s,e=o,t=-3*s+3*a-2*o-c,n=2*s-2*a+o+c}return{initCatmullRom:function(s,a,o,c,l){i(a,o,l*(o-s),l*(c-a))},initNonuniformCatmullRom:function(s,a,o,c,l,h,p){let f=(a-s)/l-(o-s)/(l+h)+(o-a)/h,m=(o-a)/h-(c-a)/(h+p)+(c-o)/p;f*=h,m*=h,i(a,o,f,m)},calc:function(s){let a=s*s;return r+e*s+t*a+n*(a*s)}}}var Yo=new N,ph=new Nu,fh=new Nu,mh=new Nu,Pr=class extends Xn{constructor(e=[],t=!1,n="centripetal",i=.5){super(),this.isCatmullRomCurve3=!0,this.type="CatmullRomCurve3",this.points=e,this.closed=t,this.curveType=n,this.tension=i}getPoint(e,t=new N){let n=t,i=this.points,s=i.length,a=(s-(this.closed?0:1))*e,o,c,l=Math.floor(a),h=a-l;this.closed?l+=l>0?0:(Math.floor(Math.abs(l)/s)+1)*s:h===0&&l===s-1&&(l=s-2,h=1),this.closed||l>0?o=i[(l-1)%s]:(Yo.subVectors(i[0],i[1]).add(i[0]),o=Yo);let p=i[l%s],f=i[(l+1)%s];if(this.closed||l+2<s?c=i[(l+2)%s]:(Yo.subVectors(i[s-1],i[s-2]).add(i[s-1]),c=Yo),this.curveType==="centripetal"||this.curveType==="chordal"){let m=this.curveType==="chordal"?.5:.25,v=Math.pow(o.distanceToSquared(p),m),g=Math.pow(p.distanceToSquared(f),m),u=Math.pow(f.distanceToSquared(c),m);g<1e-4&&(g=1),v<1e-4&&(v=g),u<1e-4&&(u=g),ph.initNonuniformCatmullRom(o.x,p.x,f.x,c.x,v,g,u),fh.initNonuniformCatmullRom(o.y,p.y,f.y,c.y,v,g,u),mh.initNonuniformCatmullRom(o.z,p.z,f.z,c.z,v,g,u)}else this.curveType==="catmullrom"&&(ph.initCatmullRom(o.x,p.x,f.x,c.x,this.tension),fh.initCatmullRom(o.y,p.y,f.y,c.y,this.tension),mh.initCatmullRom(o.z,p.z,f.z,c.z,this.tension));return n.set(ph.calc(h),fh.calc(h),mh.calc(h)),n}copy(e){super.copy(e),this.points=[];for(let t=0,n=e.points.length;t<n;t++){let i=e.points[t];this.points.push(i.clone())}return this.closed=e.closed,this.curveType=e.curveType,this.tension=e.tension,this}toJSON(){let e=super.toJSON();e.points=[];for(let t=0,n=this.points.length;t<n;t++){let i=this.points[t];e.points.push(i.toArray())}return e.closed=this.closed,e.curveType=this.curveType,e.tension=this.tension,e}fromJSON(e){super.fromJSON(e),this.points=[];for(let t=0,n=e.points.length;t<n;t++){let i=e.points[t];this.points.push(new N().fromArray(i))}return this.closed=e.closed,this.curveType=e.curveType,this.tension=e.tension,this}};function Qd(r,e,t,n,i){let s=.5*(n-e),a=.5*(i-t),o=r*r;return(2*t-2*n+s+a)*(r*o)+(-3*t+3*n-2*s-a)*o+s*r+t}function ma(r,e,t,n){return function(i,s){let a=1-i;return a*a*s}(r,e)+function(i,s){return 2*(1-i)*i*s}(r,t)+function(i,s){return i*i*s}(r,n)}function ga(r,e,t,n,i){return function(s,a){let o=1-s;return o*o*o*a}(r,e)+function(s,a){let o=1-s;return 3*o*o*s*a}(r,t)+function(s,a){return 3*(1-s)*s*s*a}(r,n)+function(s,a){return s*s*s*a}(r,i)}var La=class extends Xn{constructor(e=new Te,t=new Te,n=new Te,i=new Te){super(),this.isCubicBezierCurve=!0,this.type="CubicBezierCurve",this.v0=e,this.v1=t,this.v2=n,this.v3=i}getPoint(e,t=new Te){let n=t,i=this.v0,s=this.v1,a=this.v2,o=this.v3;return n.set(ga(e,i.x,s.x,a.x,o.x),ga(e,i.y,s.y,a.y,o.y)),n}copy(e){return super.copy(e),this.v0.copy(e.v0),this.v1.copy(e.v1),this.v2.copy(e.v2),this.v3.copy(e.v3),this}toJSON(){let e=super.toJSON();return e.v0=this.v0.toArray(),e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e.v3=this.v3.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v0.fromArray(e.v0),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this.v3.fromArray(e.v3),this}},yl=class extends Xn{constructor(e=new N,t=new N,n=new N,i=new N){super(),this.isCubicBezierCurve3=!0,this.type="CubicBezierCurve3",this.v0=e,this.v1=t,this.v2=n,this.v3=i}getPoint(e,t=new N){let n=t,i=this.v0,s=this.v1,a=this.v2,o=this.v3;return n.set(ga(e,i.x,s.x,a.x,o.x),ga(e,i.y,s.y,a.y,o.y),ga(e,i.z,s.z,a.z,o.z)),n}copy(e){return super.copy(e),this.v0.copy(e.v0),this.v1.copy(e.v1),this.v2.copy(e.v2),this.v3.copy(e.v3),this}toJSON(){let e=super.toJSON();return e.v0=this.v0.toArray(),e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e.v3=this.v3.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v0.fromArray(e.v0),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this.v3.fromArray(e.v3),this}},Da=class extends Xn{constructor(e=new Te,t=new Te){super(),this.isLineCurve=!0,this.type="LineCurve",this.v1=e,this.v2=t}getPoint(e,t=new Te){let n=t;return e===1?n.copy(this.v2):(n.copy(this.v2).sub(this.v1),n.multiplyScalar(e).add(this.v1)),n}getPointAt(e,t){return this.getPoint(e,t)}getTangent(e,t=new Te){return t.subVectors(this.v2,this.v1).normalize()}getTangentAt(e,t){return this.getTangent(e,t)}copy(e){return super.copy(e),this.v1.copy(e.v1),this.v2.copy(e.v2),this}toJSON(){let e=super.toJSON();return e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this}},xl=class extends Xn{constructor(e=new N,t=new N){super(),this.isLineCurve3=!0,this.type="LineCurve3",this.v1=e,this.v2=t}getPoint(e,t=new N){let n=t;return e===1?n.copy(this.v2):(n.copy(this.v2).sub(this.v1),n.multiplyScalar(e).add(this.v1)),n}getPointAt(e,t){return this.getPoint(e,t)}getTangent(e,t=new N){return t.subVectors(this.v2,this.v1).normalize()}getTangentAt(e,t){return this.getTangent(e,t)}copy(e){return super.copy(e),this.v1.copy(e.v1),this.v2.copy(e.v2),this}toJSON(){let e=super.toJSON();return e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this}},Ua=class extends Xn{constructor(e=new Te,t=new Te,n=new Te){super(),this.isQuadraticBezierCurve=!0,this.type="QuadraticBezierCurve",this.v0=e,this.v1=t,this.v2=n}getPoint(e,t=new Te){let n=t,i=this.v0,s=this.v1,a=this.v2;return n.set(ma(e,i.x,s.x,a.x),ma(e,i.y,s.y,a.y)),n}copy(e){return super.copy(e),this.v0.copy(e.v0),this.v1.copy(e.v1),this.v2.copy(e.v2),this}toJSON(){let e=super.toJSON();return e.v0=this.v0.toArray(),e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v0.fromArray(e.v0),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this}},Na=class extends Xn{constructor(e=new N,t=new N,n=new N){super(),this.isQuadraticBezierCurve3=!0,this.type="QuadraticBezierCurve3",this.v0=e,this.v1=t,this.v2=n}getPoint(e,t=new N){let n=t,i=this.v0,s=this.v1,a=this.v2;return n.set(ma(e,i.x,s.x,a.x),ma(e,i.y,s.y,a.y),ma(e,i.z,s.z,a.z)),n}copy(e){return super.copy(e),this.v0.copy(e.v0),this.v1.copy(e.v1),this.v2.copy(e.v2),this}toJSON(){let e=super.toJSON();return e.v0=this.v0.toArray(),e.v1=this.v1.toArray(),e.v2=this.v2.toArray(),e}fromJSON(e){return super.fromJSON(e),this.v0.fromArray(e.v0),this.v1.fromArray(e.v1),this.v2.fromArray(e.v2),this}},Oa=class extends Xn{constructor(e=[]){super(),this.isSplineCurve=!0,this.type="SplineCurve",this.points=e}getPoint(e,t=new Te){let n=t,i=this.points,s=(i.length-1)*e,a=Math.floor(s),o=s-a,c=i[a===0?a:a-1],l=i[a],h=i[a>i.length-2?i.length-1:a+1],p=i[a>i.length-3?i.length-1:a+2];return n.set(Qd(o,c.x,l.x,h.x,p.x),Qd(o,c.y,l.y,h.y,p.y)),n}copy(e){super.copy(e),this.points=[];for(let t=0,n=e.points.length;t<n;t++){let i=e.points[t];this.points.push(i.clone())}return this}toJSON(){let e=super.toJSON();e.points=[];for(let t=0,n=this.points.length;t<n;t++){let i=this.points[t];e.points.push(i.toArray())}return e}fromJSON(e){super.fromJSON(e),this.points=[];for(let t=0,n=e.points.length;t<n;t++){let i=e.points[t];this.points.push(new Te().fromArray(i))}return this}},Ml=Object.freeze({__proto__:null,ArcCurve:vl,CatmullRomCurve3:Pr,CubicBezierCurve:La,CubicBezierCurve3:yl,EllipseCurve:Es,LineCurve:Da,LineCurve3:xl,QuadraticBezierCurve:Ua,QuadraticBezierCurve3:Na,SplineCurve:Oa}),bl=class extends Xn{constructor(){super(),this.type="CurvePath",this.curves=[],this.autoClose=!1}add(e){this.curves.push(e)}closePath(){let e=this.curves[0].getPoint(0),t=this.curves[this.curves.length-1].getPoint(1);if(!e.equals(t)){let n=e.isVector2===!0?"LineCurve":"LineCurve3";this.curves.push(new Ml[n](t,e))}return this}getPoint(e,t){let n=e*this.getLength(),i=this.getCurveLengths(),s=0;for(;s<i.length;){if(i[s]>=n){let a=i[s]-n,o=this.curves[s],c=o.getLength(),l=c===0?0:1-a/c;return o.getPointAt(l,t)}s++}return null}getLength(){let e=this.getCurveLengths();return e[e.length-1]}updateArcLengths(){this.needsUpdate=!0,this.cacheLengths=null,this.getCurveLengths()}getCurveLengths(){if(this.cacheLengths&&this.cacheLengths.length===this.curves.length)return this.cacheLengths;let e=[],t=0;for(let n=0,i=this.curves.length;n<i;n++)t+=this.curves[n].getLength(),e.push(t);return this.cacheLengths=e,e}getSpacedPoints(e=40){let t=[];for(let n=0;n<=e;n++)t.push(this.getPoint(n/e));return this.autoClose&&t.push(t[0]),t}getPoints(e=12){let t=[],n;for(let i=0,s=this.curves;i<s.length;i++){let a=s[i],o=a.isEllipseCurve?2*e:a.isLineCurve||a.isLineCurve3?1:a.isSplineCurve?e*a.points.length:e,c=a.getPoints(o);for(let l=0;l<c.length;l++){let h=c[l];n&&n.equals(h)||(t.push(h),n=h)}}return this.autoClose&&t.length>1&&!t[t.length-1].equals(t[0])&&t.push(t[0]),t}copy(e){super.copy(e),this.curves=[];for(let t=0,n=e.curves.length;t<n;t++){let i=e.curves[t];this.curves.push(i.clone())}return this.autoClose=e.autoClose,this}toJSON(){let e=super.toJSON();e.autoClose=this.autoClose,e.curves=[];for(let t=0,n=this.curves.length;t<n;t++){let i=this.curves[t];e.curves.push(i.toJSON())}return e}fromJSON(e){super.fromJSON(e),this.autoClose=e.autoClose,this.curves=[];for(let t=0,n=e.curves.length;t<n;t++){let i=e.curves[t];this.curves.push(new Ml[i.type]().fromJSON(i))}return this}},Fa=class extends bl{constructor(e){super(),this.type="Path",this.currentPoint=new Te,e&&this.setFromPoints(e)}setFromPoints(e){this.moveTo(e[0].x,e[0].y);for(let t=1,n=e.length;t<n;t++)this.lineTo(e[t].x,e[t].y);return this}moveTo(e,t){return this.currentPoint.set(e,t),this}lineTo(e,t){let n=new Da(this.currentPoint.clone(),new Te(e,t));return this.curves.push(n),this.currentPoint.set(e,t),this}quadraticCurveTo(e,t,n,i){let s=new Ua(this.currentPoint.clone(),new Te(e,t),new Te(n,i));return this.curves.push(s),this.currentPoint.set(n,i),this}bezierCurveTo(e,t,n,i,s,a){let o=new La(this.currentPoint.clone(),new Te(e,t),new Te(n,i),new Te(s,a));return this.curves.push(o),this.currentPoint.set(s,a),this}splineThru(e){let t=[this.currentPoint.clone()].concat(e),n=new Oa(t);return this.curves.push(n),this.currentPoint.copy(e[e.length-1]),this}arc(e,t,n,i,s,a){let o=this.currentPoint.x,c=this.currentPoint.y;return this.absarc(e+o,t+c,n,i,s,a),this}absarc(e,t,n,i,s,a){return this.absellipse(e,t,n,n,i,s,a),this}ellipse(e,t,n,i,s,a,o,c){let l=this.currentPoint.x,h=this.currentPoint.y;return this.absellipse(e+l,t+h,n,i,s,a,o,c),this}absellipse(e,t,n,i,s,a,o,c){let l=new Es(e,t,n,i,s,a,o,c);if(this.curves.length>0){let p=l.getPoint(0);p.equals(this.currentPoint)||this.lineTo(p.x,p.y)}this.curves.push(l);let h=l.getPoint(1);return this.currentPoint.copy(h),this}copy(e){return super.copy(e),this.currentPoint.copy(e.currentPoint),this}toJSON(){let e=super.toJSON();return e.currentPoint=this.currentPoint.toArray(),e}fromJSON(e){return super.fromJSON(e),this.currentPoint.fromArray(e.currentPoint),this}},Ba=class extends Fa{constructor(e){super(e),this.uuid=Br(),this.type="Shape",this.holes=[]}getPointsHoles(e){let t=[];for(let n=0,i=this.holes.length;n<i;n++)t[n]=this.holes[n].getPoints(e);return t}extractPoints(e){return{shape:this.getPoints(e),holes:this.getPointsHoles(e)}}copy(e){super.copy(e),this.holes=[];for(let t=0,n=e.holes.length;t<n;t++){let i=e.holes[t];this.holes.push(i.clone())}return this}toJSON(){let e=super.toJSON();e.uuid=this.uuid,e.holes=[];for(let t=0,n=this.holes.length;t<n;t++){let i=this.holes[t];e.holes.push(i.toJSON())}return e}fromJSON(e){super.fromJSON(e),this.uuid=e.uuid,this.holes=[];for(let t=0,n=e.holes.length;t<n;t++){let i=e.holes[t];this.holes.push(new Fa().fromJSON(i))}return this}};function Rm(r,e,t=2){let n=e&&e.length,i=n?e[0]*t:r.length,s=ep(r,0,i,t,!0),a=[];if(!s||s.next===s.prev)return a;let o,c,l;if(n&&(s=function(h,p,f,m){let v=[];for(let g=0,u=p.length;g<u;g++){let y=ep(h,p[g]*m,g<u-1?p[g+1]*m:h.length,m,!1);y===y.next&&(y.steiner=!0),v.push(Fm(y))}v.sort(Um);for(let g=0;g<v.length;g++)f=Nm(v[g],f);return f}(r,e,s,t)),r.length>80*t){o=r[0],c=r[1];let h=o,p=c;for(let f=t;f<i;f+=t){let m=r[f],v=r[f+1];m<o&&(o=m),v<c&&(c=v),m>h&&(h=m),v>p&&(p=v)}l=Math.max(h-o,p-c),l=l!==0?32767/l:0}return za(s,a,t,o,c,l,0),a}function ep(r,e,t,n,i){let s;if(i===function(a,o,c,l){let h=0;for(let p=o,f=c-l;p<c;p+=l)h+=(a[f]-a[p])*(a[p+1]+a[f+1]),f=p;return h}(r,e,t,n)>0)for(let a=e;a<t;a+=n)s=tp(a/n|0,r[a],r[a+1],s);else for(let a=t-n;a>=e;a-=n)s=tp(a/n|0,r[a],r[a+1],s);return s&&ws(s,s.next)&&(Ha(s),s=s.next),s}function Ir(r,e){if(!r)return r;e||(e=r);let t,n=r;do if(t=!1,n.steiner||!ws(n,n.next)&&Ft(n.prev,n,n.next)!==0)n=n.next;else{if(Ha(n),n=e=n.prev,n===n.next)break;t=!0}while(t||n!==e);return e}function za(r,e,t,n,i,s,a){if(!r)return;!a&&s&&function(c,l,h,p){let f=c;do f.z===0&&(f.z=wh(f.x,f.y,l,h,p)),f.prevZ=f.prev,f.nextZ=f.next,f=f.next;while(f!==c);f.prevZ.nextZ=null,f.prevZ=null,function(m){let v,g=1;do{let u,y=m;m=null;let x=null;for(v=0;y;){v++;let S=y,E=0;for(let P=0;P<g&&(E++,S=S.nextZ,S);P++);let R=g;for(;E>0||R>0&&S;)E!==0&&(R===0||!S||y.z<=S.z)?(u=y,y=y.nextZ,E--):(u=S,S=S.nextZ,R--),x?x.nextZ=u:m=u,u.prevZ=x,x=u;y=S}x.nextZ=null,g*=2}while(v>1)}(f)}(r,n,i,s);let o=r;for(;r.prev!==r.next;){let c=r.prev,l=r.next;if(s?Im(r,n,i,s):Pm(r))e.push(c.i,r.i,l.i),Ha(r),r=l.next,o=l.next;else if((r=l)===o){a?a===1?za(r=Lm(Ir(r),e),e,t,n,i,s,2):a===2&&Dm(r,e,t,n,i,s):za(Ir(r),e,t,n,i,s,1);break}}}function Pm(r){let e=r.prev,t=r,n=r.next;if(Ft(e,t,n)>=0)return!1;let i=e.x,s=t.x,a=n.x,o=e.y,c=t.y,l=n.y,h=Math.min(i,s,a),p=Math.min(o,c,l),f=Math.max(i,s,a),m=Math.max(o,c,l),v=n.next;for(;v!==e;){if(v.x>=h&&v.x<=f&&v.y>=p&&v.y<=m&&pa(i,o,s,c,a,l,v.x,v.y)&&Ft(v.prev,v,v.next)>=0)return!1;v=v.next}return!0}function Im(r,e,t,n){let i=r.prev,s=r,a=r.next;if(Ft(i,s,a)>=0)return!1;let o=i.x,c=s.x,l=a.x,h=i.y,p=s.y,f=a.y,m=Math.min(o,c,l),v=Math.min(h,p,f),g=Math.max(o,c,l),u=Math.max(h,p,f),y=wh(m,v,e,t,n),x=wh(g,u,e,t,n),S=r.prevZ,E=r.nextZ;for(;S&&S.z>=y&&E&&E.z<=x;){if(S.x>=m&&S.x<=g&&S.y>=v&&S.y<=u&&S!==i&&S!==a&&pa(o,h,c,p,l,f,S.x,S.y)&&Ft(S.prev,S,S.next)>=0||(S=S.prevZ,E.x>=m&&E.x<=g&&E.y>=v&&E.y<=u&&E!==i&&E!==a&&pa(o,h,c,p,l,f,E.x,E.y)&&Ft(E.prev,E,E.next)>=0))return!1;E=E.nextZ}for(;S&&S.z>=y;){if(S.x>=m&&S.x<=g&&S.y>=v&&S.y<=u&&S!==i&&S!==a&&pa(o,h,c,p,l,f,S.x,S.y)&&Ft(S.prev,S,S.next)>=0)return!1;S=S.prevZ}for(;E&&E.z<=x;){if(E.x>=m&&E.x<=g&&E.y>=v&&E.y<=u&&E!==i&&E!==a&&pa(o,h,c,p,l,f,E.x,E.y)&&Ft(E.prev,E,E.next)>=0)return!1;E=E.nextZ}return!0}function Lm(r,e){let t=r;do{let n=t.prev,i=t.next.next;!ws(n,i)&&tf(n,t,t.next,i)&&ka(n,i)&&ka(i,n)&&(e.push(n.i,t.i,i.i),Ha(t),Ha(t.next),t=r=i),t=t.next}while(t!==r);return Ir(t)}function Dm(r,e,t,n,i,s){let a=r;do{let o=a.next.next;for(;o!==a.prev;){if(a.i!==o.i&&Bm(a,o)){let c=nf(a,o);return a=Ir(a,a.next),c=Ir(c,c.next),za(a,e,t,n,i,s,0),void za(c,e,t,n,i,s,0)}o=o.next}a=a.next}while(a!==r)}function Um(r,e){let t=r.x-e.x;return t===0&&(t=r.y-e.y,t===0)&&(t=(r.next.y-r.y)/(r.next.x-r.x)-(e.next.y-e.y)/(e.next.x-e.x)),t}function Nm(r,e){let t=function(i,s){let a=s,o=i.x,c=i.y,l,h=-1/0;if(ws(i,a))return a;do{if(ws(i,a.next))return a.next;if(c<=a.y&&c>=a.next.y&&a.next.y!==a.y){let g=a.x+(c-a.y)*(a.next.x-a.x)/(a.next.y-a.y);if(g<=o&&g>h&&(h=g,l=a.x<a.next.x?a:a.next,g===o))return l}a=a.next}while(a!==s);if(!l)return null;let p=l,f=l.x,m=l.y,v=1/0;a=l;do{if(o>=a.x&&a.x>=f&&o!==a.x&&ef(c<m?o:h,c,f,m,c<m?h:o,c,a.x,a.y)){let g=Math.abs(c-a.y)/(o-a.x);ka(a,i)&&(g<v||g===v&&(a.x>l.x||a.x===l.x&&Om(l,a)))&&(l=a,v=g)}a=a.next}while(a!==p);return l}(r,e);if(!t)return e;let n=nf(t,r);return Ir(n,n.next),Ir(t,t.next)}function Om(r,e){return Ft(r.prev,r,e.prev)<0&&Ft(e.next,r,r.next)<0}function wh(r,e,t,n,i){return(r=1431655765&((r=858993459&((r=252645135&((r=16711935&((r=(r-t)*i|0)|r<<8))|r<<4))|r<<2))|r<<1))|(e=1431655765&((e=858993459&((e=252645135&((e=16711935&((e=(e-n)*i|0)|e<<8))|e<<4))|e<<2))|e<<1))<<1}function Fm(r){let e=r,t=r;do(e.x<t.x||e.x===t.x&&e.y<t.y)&&(t=e),e=e.next;while(e!==r);return t}function ef(r,e,t,n,i,s,a,o){return(i-a)*(e-o)>=(r-a)*(s-o)&&(r-a)*(n-o)>=(t-a)*(e-o)&&(t-a)*(s-o)>=(i-a)*(n-o)}function pa(r,e,t,n,i,s,a,o){return!(r===a&&e===o)&&ef(r,e,t,n,i,s,a,o)}function Bm(r,e){return r.next.i!==e.i&&r.prev.i!==e.i&&!function(t,n){let i=t;do{if(i.i!==t.i&&i.next.i!==t.i&&i.i!==n.i&&i.next.i!==n.i&&tf(i,i.next,t,n))return!0;i=i.next}while(i!==t);return!1}(r,e)&&(ka(r,e)&&ka(e,r)&&function(t,n){let i=t,s=!1,a=(t.x+n.x)/2,o=(t.y+n.y)/2;do i.y>o!=i.next.y>o&&i.next.y!==i.y&&a<(i.next.x-i.x)*(o-i.y)/(i.next.y-i.y)+i.x&&(s=!s),i=i.next;while(i!==t);return s}(r,e)&&(Ft(r.prev,r,e.prev)||Ft(r,e.prev,e))||ws(r,e)&&Ft(r.prev,r,r.next)>0&&Ft(e.prev,e,e.next)>0)}function Ft(r,e,t){return(e.y-r.y)*(t.x-e.x)-(e.x-r.x)*(t.y-e.y)}function ws(r,e){return r.x===e.x&&r.y===e.y}function tf(r,e,t,n){let i=Jo(Ft(r,e,t)),s=Jo(Ft(r,e,n)),a=Jo(Ft(t,n,r)),o=Jo(Ft(t,n,e));return i!==s&&a!==o||!(i!==0||!Zo(r,t,e))||!(s!==0||!Zo(r,n,e))||!(a!==0||!Zo(t,r,n))||!(o!==0||!Zo(t,e,n))}function Zo(r,e,t){return e.x<=Math.max(r.x,t.x)&&e.x>=Math.min(r.x,t.x)&&e.y<=Math.max(r.y,t.y)&&e.y>=Math.min(r.y,t.y)}function Jo(r){return r>0?1:r<0?-1:0}function ka(r,e){return Ft(r.prev,r,r.next)<0?Ft(r,e,r.next)>=0&&Ft(r,r.prev,e)>=0:Ft(r,e,r.prev)<0||Ft(r,r.next,e)<0}function nf(r,e){let t=Ah(r.i,r.x,r.y),n=Ah(e.i,e.x,e.y),i=r.next,s=e.prev;return r.next=e,e.prev=r,t.next=i,i.prev=t,n.next=t,t.prev=n,s.next=n,n.prev=s,n}function tp(r,e,t,n){let i=Ah(r,e,t);return n?(i.next=n.next,i.prev=n,n.next.prev=i,n.next=i):(i.prev=i,i.next=i),i}function Ha(r){r.next.prev=r.prev,r.prev.next=r.next,r.prevZ&&(r.prevZ.nextZ=r.nextZ),r.nextZ&&(r.nextZ.prevZ=r.prevZ)}function Ah(r,e,t){return{i:r,x:e,y:t,prev:null,next:null,z:0,prevZ:null,nextZ:null,steiner:!1}}var Ch=class{static triangulate(e,t,n=2){return Rm(e,t,n)}},Mi=class r{static area(e){let t=e.length,n=0;for(let i=t-1,s=0;s<t;i=s++)n+=e[i].x*e[s].y-e[s].x*e[i].y;return .5*n}static isClockWise(e){return r.area(e)<0}static triangulateShape(e,t){let n=[],i=[],s=[];np(e),ip(n,e);let a=e.length;t.forEach(np);for(let c=0;c<t.length;c++)i.push(a),a+=t[c].length,ip(n,t[c]);let o=Ch.triangulate(n,i);for(let c=0;c<o.length;c+=3)s.push(o.slice(c,c+3));return s}};function np(r){let e=r.length;e>2&&r[e-1].equals(r[0])&&r.pop()}function ip(r,e){for(let t=0;t<e.length;t++)r.push(e[t].x),r.push(e[t].y)}var Sl=class r extends St{constructor(e=new Ba([new Te(.5,.5),new Te(-.5,.5),new Te(-.5,-.5),new Te(.5,-.5)]),t={}){super(),this.type="ExtrudeGeometry",this.parameters={shapes:e,options:t},e=Array.isArray(e)?e:[e];let n=this,i=[],s=[];for(let o=0,c=e.length;o<c;o++)a(e[o]);function a(o){let c=[],l=t.curveSegments!==void 0?t.curveSegments:12,h=t.steps!==void 0?t.steps:1,p=t.depth!==void 0?t.depth:1,f=t.bevelEnabled===void 0||t.bevelEnabled,m=t.bevelThickness!==void 0?t.bevelThickness:.2,v=t.bevelSize!==void 0?t.bevelSize:m-.1,g=t.bevelOffset!==void 0?t.bevelOffset:0,u=t.bevelSegments!==void 0?t.bevelSegments:3,y=t.extrudePath,x=t.UVGenerator!==void 0?t.UVGenerator:zm,S,E,R,P,V,X=!1;if(y){S=y.getSpacedPoints(h),X=!0,f=!1;let B=!!y.isCatmullRomCurve3&&y.closed;E=y.computeFrenetFrames(h,B),R=new N,P=new N,V=new N}f||(u=0,m=0,v=0,g=0);let H=o.extractPoints(l),se=H.shape,oe=H.holes;if(!Mi.isClockWise(se)){se=se.reverse();for(let B=0,w=oe.length;B<w;B++){let k=oe[B];Mi.isClockWise(k)&&(oe[B]=k.reverse())}}function te(B){let w=10000000000000001e-36,k=B[0];for(let Y=1;Y<=B.length;Y++){let K=Y%B.length,T=B[K],G=T.x-k.x,L=T.y-k.y,C=G*G+L*L,z=Math.max(Math.abs(T.x),Math.abs(T.y),Math.abs(k.x),Math.abs(k.y));C<=w*z*z?(B.splice(K,1),Y--):k=T}}te(se),oe.forEach(te);let ne=oe.length,j=se;for(let B=0;B<ne;B++){let w=oe[B];se=se.concat(w)}function J(B,w,k){return w||rt("ExtrudeGeometry: vec does not exist"),B.clone().addScaledVector(w,k)}let ie=se.length;function re(B,w,k){let Y,K,T,G=B.x-w.x,L=B.y-w.y,C=k.x-B.x,z=k.y-B.y,O=G*G+L*L,W=G*z-L*C;if(Math.abs(W)>Number.EPSILON){let Q=Math.sqrt(O),xe=Math.sqrt(C*C+z*z),fe=w.x-L/Q,Pe=w.y+G/Q,Be=((k.x-z/xe-fe)*z-(k.y+C/xe-Pe)*C)/(G*z-L*C);Y=fe+G*Be-B.x,K=Pe+L*Be-B.y;let he=Y*Y+K*K;if(he<=2)return new Te(Y,K);T=Math.sqrt(he/2)}else{let Q=!1;G>Number.EPSILON?C>Number.EPSILON&&(Q=!0):G<-Number.EPSILON?C<-Number.EPSILON&&(Q=!0):Math.sign(L)===Math.sign(z)&&(Q=!0),Q?(Y=-L,K=G,T=Math.sqrt(O)):(Y=G,K=L,T=Math.sqrt(O/2))}return new Te(Y/T,K/T)}let Se=[];for(let B=0,w=j.length,k=w-1,Y=B+1;B<w;B++,k++,Y++)k===w&&(k=0),Y===w&&(Y=0),Se[B]=re(j[B],j[k],j[Y]);let we=[],ze,Ae,Ie=Se.concat();for(let B=0,w=ne;B<w;B++){let k=oe[B];ze=[];for(let Y=0,K=k.length,T=K-1,G=Y+1;Y<K;Y++,T++,G++)T===K&&(T=0),G===K&&(G=0),ze[Y]=re(k[Y],k[T],k[G]);we.push(ze),Ie=Ie.concat(ze)}if(u===0)Ae=Mi.triangulateShape(j,oe);else{let B=[],w=[];for(let k=0;k<u;k++){let Y=k/u,K=m*Math.cos(Y*Math.PI/2),T=v*Math.sin(Y*Math.PI/2)+g;for(let G=0,L=j.length;G<L;G++){let C=J(j[G],Se[G],T);Ye(C.x,C.y,-K),Y===0&&B.push(C)}for(let G=0,L=ne;G<L;G++){let C=oe[G];ze=we[G];let z=[];for(let O=0,W=C.length;O<W;O++){let Q=J(C[O],ze[O],T);Ye(Q.x,Q.y,-K),Y===0&&z.push(Q)}Y===0&&w.push(z)}}Ae=Mi.triangulateShape(B,w)}let ve=Ae.length,Ee=v+g;for(let B=0;B<ie;B++){let w=f?J(se[B],Ie[B],Ee):se[B];X?(P.copy(E.normals[0]).multiplyScalar(w.x),R.copy(E.binormals[0]).multiplyScalar(w.y),V.copy(S[0]).add(P).add(R),Ye(V.x,V.y,V.z)):Ye(w.x,w.y,0)}for(let B=1;B<=h;B++)for(let w=0;w<ie;w++){let k=f?J(se[w],Ie[w],Ee):se[w];X?(P.copy(E.normals[B]).multiplyScalar(k.x),R.copy(E.binormals[B]).multiplyScalar(k.y),V.copy(S[B]).add(P).add(R),Ye(V.x,V.y,V.z)):Ye(k.x,k.y,p/h*B)}for(let B=u-1;B>=0;B--){let w=B/u,k=m*Math.cos(w*Math.PI/2),Y=v*Math.sin(w*Math.PI/2)+g;for(let K=0,T=j.length;K<T;K++){let G=J(j[K],Se[K],Y);Ye(G.x,G.y,p+k)}for(let K=0,T=oe.length;K<T;K++){let G=oe[K];ze=we[K];for(let L=0,C=G.length;L<C;L++){let z=J(G[L],ze[L],Y);X?Ye(z.x,z.y+S[h-1].y,S[h-1].x+k):Ye(z.x,z.y,p+k)}}}function Le(B,w){let k=B.length;for(;--k>=0;){let Y=k,K=k-1;K<0&&(K=B.length-1);for(let T=0,G=h+2*u;T<G;T++){let L=ie*T,C=ie*(T+1);U(w+Y+L,w+K+L,w+K+C,w+Y+C)}}}function Ye(B,w,k){c.push(B),c.push(w),c.push(k)}function We(B,w,k){D(B),D(w),D(k);let Y=i.length/3,K=x.generateTopUV(n,i,Y-3,Y-2,Y-1);$(K[0]),$(K[1]),$(K[2])}function U(B,w,k,Y){D(B),D(w),D(Y),D(w),D(k),D(Y);let K=i.length/3,T=x.generateSideWallUV(n,i,K-6,K-3,K-2,K-1);$(T[0]),$(T[1]),$(T[3]),$(T[1]),$(T[2]),$(T[3])}function D(B){i.push(c[3*B+0]),i.push(c[3*B+1]),i.push(c[3*B+2])}function $(B){s.push(B.x),s.push(B.y)}(function(){let B=i.length/3;if(f){let w=0,k=ie*w;for(let Y=0;Y<ve;Y++){let K=Ae[Y];We(K[2]+k,K[1]+k,K[0]+k)}w=h+2*u,k=ie*w;for(let Y=0;Y<ve;Y++){let K=Ae[Y];We(K[0]+k,K[1]+k,K[2]+k)}}else{for(let w=0;w<ve;w++){let k=Ae[w];We(k[2],k[1],k[0])}for(let w=0;w<ve;w++){let k=Ae[w];We(k[0]+ie*h,k[1]+ie*h,k[2]+ie*h)}}n.addGroup(B,i.length/3-B,0)})(),function(){let B=i.length/3,w=0;Le(j,w),w+=j.length;for(let k=0,Y=oe.length;k<Y;k++){let K=oe[k];Le(K,w),w+=K.length}n.addGroup(B,i.length/3-B,1)}()}this.setAttribute("position",new $e(i,3)),this.setAttribute("uv",new $e(s,2)),this.computeVertexNormals()}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}toJSON(){let e=super.toJSON();return function(t,n,i){if(i.shapes=[],Array.isArray(t))for(let s=0,a=t.length;s<a;s++){let o=t[s];i.shapes.push(o.uuid)}else i.shapes.push(t.uuid);return i.options=Object.assign({},n),n.extrudePath!==void 0&&(i.options.extrudePath=n.extrudePath.toJSON()),i}(this.parameters.shapes,this.parameters.options,e)}static fromJSON(e,t){let n=[];for(let s=0,a=e.shapes.length;s<a;s++){let o=t[e.shapes[s]];n.push(o)}let i=e.options.extrudePath;return i!==void 0&&(e.options.extrudePath=new Ml[i.type]().fromJSON(i)),new r(n,e.options)}},zm={generateTopUV:function(r,e,t,n,i){let s=e[3*t],a=e[3*t+1],o=e[3*n],c=e[3*n+1],l=e[3*i],h=e[3*i+1];return[new Te(s,a),new Te(o,c),new Te(l,h)]},generateSideWallUV:function(r,e,t,n,i,s){let a=e[3*t],o=e[3*t+1],c=e[3*t+2],l=e[3*n],h=e[3*n+1],p=e[3*n+2],f=e[3*i],m=e[3*i+1],v=e[3*i+2],g=e[3*s],u=e[3*s+1],y=e[3*s+2];return Math.abs(o-h)<Math.abs(a-l)?[new Te(a,1-c),new Te(l,1-p),new Te(f,1-v),new Te(g,1-y)]:[new Te(o,1-c),new Te(h,1-p),new Te(m,1-v),new Te(u,1-y)]}},Tl=class r extends cr{constructor(e=1,t=0){let n=(1+Math.sqrt(5))/2;super([-1,n,0,1,n,0,-1,-n,0,1,-n,0,0,-1,n,0,1,n,0,-1,-n,0,1,-n,n,0,-1,n,0,1,-n,0,-1,-n,0,1],[0,11,5,0,5,1,0,1,7,0,7,10,0,10,11,1,5,9,5,11,4,11,10,2,10,7,6,7,1,8,3,9,4,3,4,2,3,2,6,3,6,8,3,8,9,4,9,5,2,4,11,6,2,10,8,6,7,9,8,1],e,t),this.type="IcosahedronGeometry",this.parameters={radius:e,detail:t}}static fromJSON(e){return new r(e.radius,e.detail)}},El=class r extends St{constructor(e=[new Te(0,-.5),new Te(.5,0),new Te(0,.5)],t=12,n=0,i=2*Math.PI){super(),this.type="LatheGeometry",this.parameters={points:e,segments:t,phiStart:n,phiLength:i},t=Math.floor(t),i=ut(i,0,2*Math.PI);let s=[],a=[],o=[],c=[],l=[],h=1/t,p=new N,f=new Te,m=new N,v=new N,g=new N,u=0,y=0;for(let x=0;x<=e.length-1;x++)switch(x){case 0:u=e[x+1].x-e[x].x,y=e[x+1].y-e[x].y,m.x=1*y,m.y=-u,m.z=0*y,g.copy(m),m.normalize(),c.push(m.x,m.y,m.z);break;case e.length-1:c.push(g.x,g.y,g.z);break;default:u=e[x+1].x-e[x].x,y=e[x+1].y-e[x].y,m.x=1*y,m.y=-u,m.z=0*y,v.copy(m),m.x+=g.x,m.y+=g.y,m.z+=g.z,m.normalize(),c.push(m.x,m.y,m.z),g.copy(v)}for(let x=0;x<=t;x++){let S=n+x*h*i,E=Math.sin(S),R=Math.cos(S);for(let P=0;P<=e.length-1;P++){p.x=e[P].x*E,p.y=e[P].y,p.z=e[P].x*R,a.push(p.x,p.y,p.z),f.x=x/t,f.y=P/(e.length-1),o.push(f.x,f.y);let V=c[3*P+0]*E,X=c[3*P+1],H=c[3*P+0]*R;l.push(V,X,H)}}for(let x=0;x<t;x++)for(let S=0;S<e.length-1;S++){let E=S+x*e.length,R=E,P=E+e.length,V=E+e.length+1,X=E+1;s.push(R,P,X),s.push(V,X,P)}this.setIndex(s),this.setAttribute("position",new $e(a,3)),this.setAttribute("uv",new $e(o,2)),this.setAttribute("normal",new $e(l,3))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.points,e.segments,e.phiStart,e.phiLength)}},wl=class r extends cr{constructor(e=1,t=0){super([1,0,0,-1,0,0,0,1,0,0,-1,0,0,0,1,0,0,-1],[0,2,4,0,4,3,0,3,5,0,5,2,1,2,5,1,5,3,1,3,4,1,4,2],e,t),this.type="OctahedronGeometry",this.parameters={radius:e,detail:t}}static fromJSON(e){return new r(e.radius,e.detail)}},vn=class r extends St{constructor(e=1,t=1,n=1,i=1){super(),this.type="PlaneGeometry",this.parameters={width:e,height:t,widthSegments:n,heightSegments:i};let s=e/2,a=t/2,o=Math.floor(n),c=Math.floor(i),l=o+1,h=c+1,p=e/o,f=t/c,m=[],v=[],g=[],u=[];for(let y=0;y<h;y++){let x=y*f-a;for(let S=0;S<l;S++){let E=S*p-s;v.push(E,-x,0),g.push(0,0,1),u.push(S/o),u.push(1-y/c)}}for(let y=0;y<c;y++)for(let x=0;x<o;x++){let S=x+l*y,E=x+l*(y+1),R=x+1+l*(y+1),P=x+1+l*y;m.push(S,E,P),m.push(E,R,P)}this.setIndex(m),this.setAttribute("position",new $e(v,3)),this.setAttribute("normal",new $e(g,3)),this.setAttribute("uv",new $e(u,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.width,e.height,e.widthSegments,e.heightSegments)}},Al=class r extends St{constructor(e=.5,t=1,n=32,i=1,s=0,a=2*Math.PI){super(),this.type="RingGeometry",this.parameters={innerRadius:e,outerRadius:t,thetaSegments:n,phiSegments:i,thetaStart:s,thetaLength:a},n=Math.max(3,n);let o=[],c=[],l=[],h=[],p=e,f=(t-e)/(i=Math.max(1,i)),m=new N,v=new Te;for(let g=0;g<=i;g++){for(let u=0;u<=n;u++){let y=s+u/n*a;m.x=p*Math.cos(y),m.y=p*Math.sin(y),c.push(m.x,m.y,m.z),l.push(0,0,1),v.x=(m.x/t+1)/2,v.y=(m.y/t+1)/2,h.push(v.x,v.y)}p+=f}for(let g=0;g<i;g++){let u=g*(n+1);for(let y=0;y<n;y++){let x=y+u,S=x,E=x+n+1,R=x+n+2,P=x+1;o.push(S,E,P),o.push(E,R,P)}}this.setIndex(o),this.setAttribute("position",new $e(c,3)),this.setAttribute("normal",new $e(l,3)),this.setAttribute("uv",new $e(h,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.innerRadius,e.outerRadius,e.thetaSegments,e.phiSegments,e.thetaStart,e.thetaLength)}},Cl=class r extends St{constructor(e=new Ba([new Te(0,.5),new Te(-.5,-.5),new Te(.5,-.5)]),t=12){super(),this.type="ShapeGeometry",this.parameters={shapes:e,curveSegments:t};let n=[],i=[],s=[],a=[],o=0,c=0;if(Array.isArray(e)===!1)l(e);else for(let h=0;h<e.length;h++)l(e[h]),this.addGroup(o,c,h),o+=c,c=0;function l(h){let p=i.length/3,f=h.extractPoints(t),m=f.shape,v=f.holes;Mi.isClockWise(m)===!1&&(m=m.reverse());for(let u=0,y=v.length;u<y;u++){let x=v[u];Mi.isClockWise(x)===!0&&(v[u]=x.reverse())}let g=Mi.triangulateShape(m,v);for(let u=0,y=v.length;u<y;u++){let x=v[u];m=m.concat(x)}for(let u=0,y=m.length;u<y;u++){let x=m[u];i.push(x.x,x.y,0),s.push(0,0,1),a.push(x.x,x.y)}for(let u=0,y=g.length;u<y;u++){let x=g[u],S=x[0]+p,E=x[1]+p,R=x[2]+p;n.push(S,E,R),c+=3}}this.setIndex(n),this.setAttribute("position",new $e(i,3)),this.setAttribute("normal",new $e(s,3)),this.setAttribute("uv",new $e(a,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}toJSON(){let e=super.toJSON();return function(t,n){if(n.shapes=[],Array.isArray(t))for(let i=0,s=t.length;i<s;i++){let a=t[i];n.shapes.push(a.uuid)}else n.shapes.push(t.uuid);return n}(this.parameters.shapes,e)}static fromJSON(e,t){let n=[];for(let i=0,s=e.shapes.length;i<s;i++){let a=t[e.shapes[i]];n.push(a)}return new r(n,e.curveSegments)}},Rl=class r extends St{constructor(e=1,t=32,n=16,i=0,s=2*Math.PI,a=0,o=Math.PI){super(),this.type="SphereGeometry",this.parameters={radius:e,widthSegments:t,heightSegments:n,phiStart:i,phiLength:s,thetaStart:a,thetaLength:o},t=Math.max(3,Math.floor(t)),n=Math.max(2,Math.floor(n));let c=Math.min(a+o,Math.PI),l=0,h=[],p=new N,f=new N,m=[],v=[],g=[],u=[];for(let y=0;y<=n;y++){let x=[],S=y/n,E=0;y===0&&a===0?E=.5/t:y===n&&c===Math.PI&&(E=-.5/t);for(let R=0;R<=t;R++){let P=R/t;p.x=-e*Math.cos(i+P*s)*Math.sin(a+S*o),p.y=e*Math.cos(a+S*o),p.z=e*Math.sin(i+P*s)*Math.sin(a+S*o),v.push(p.x,p.y,p.z),f.copy(p).normalize(),g.push(f.x,f.y,f.z),u.push(P+E,1-S),x.push(l++)}h.push(x)}for(let y=0;y<n;y++)for(let x=0;x<t;x++){let S=h[y][x+1],E=h[y][x],R=h[y+1][x],P=h[y+1][x+1];(y!==0||a>0)&&m.push(S,E,P),(y!==n-1||c<Math.PI)&&m.push(E,R,P)}this.setIndex(m),this.setAttribute("position",new $e(v,3)),this.setAttribute("normal",new $e(g,3)),this.setAttribute("uv",new $e(u,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radius,e.widthSegments,e.heightSegments,e.phiStart,e.phiLength,e.thetaStart,e.thetaLength)}},Pl=class r extends cr{constructor(e=1,t=0){super([1,1,1,-1,-1,1,-1,1,-1,1,-1,-1],[2,1,0,0,3,2,1,3,0,2,3,1],e,t),this.type="TetrahedronGeometry",this.parameters={radius:e,detail:t}}static fromJSON(e){return new r(e.radius,e.detail)}},Il=class r extends St{constructor(e=1,t=.4,n=12,i=48,s=2*Math.PI,a=0,o=2*Math.PI){super(),this.type="TorusGeometry",this.parameters={radius:e,tube:t,radialSegments:n,tubularSegments:i,arc:s,thetaStart:a,thetaLength:o},n=Math.floor(n),i=Math.floor(i);let c=[],l=[],h=[],p=[],f=new N,m=new N,v=new N;for(let g=0;g<=n;g++){let u=a+g/n*o;for(let y=0;y<=i;y++){let x=y/i*s;m.x=(e+t*Math.cos(u))*Math.cos(x),m.y=(e+t*Math.cos(u))*Math.sin(x),m.z=t*Math.sin(u),l.push(m.x,m.y,m.z),f.x=e*Math.cos(x),f.y=e*Math.sin(x),v.subVectors(m,f).normalize(),h.push(v.x,v.y,v.z),p.push(y/i),p.push(g/n)}}for(let g=1;g<=n;g++)for(let u=1;u<=i;u++){let y=(i+1)*g+u-1,x=(i+1)*(g-1)+u-1,S=(i+1)*(g-1)+u,E=(i+1)*g+u;c.push(y,x,E),c.push(x,S,E)}this.setIndex(c),this.setAttribute("position",new $e(l,3)),this.setAttribute("normal",new $e(h,3)),this.setAttribute("uv",new $e(p,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radius,e.tube,e.radialSegments,e.tubularSegments,e.arc)}},Ll=class r extends St{constructor(e=1,t=.4,n=64,i=8,s=2,a=3){super(),this.type="TorusKnotGeometry",this.parameters={radius:e,tube:t,tubularSegments:n,radialSegments:i,p:s,q:a},n=Math.floor(n),i=Math.floor(i);let o=[],c=[],l=[],h=[],p=new N,f=new N,m=new N,v=new N,g=new N,u=new N,y=new N;for(let S=0;S<=n;++S){let E=S/n*s*Math.PI*2;x(E,s,a,e,m),x(E+.01,s,a,e,v),u.subVectors(v,m),y.addVectors(v,m),g.crossVectors(u,y),y.crossVectors(g,u),g.normalize(),y.normalize();for(let R=0;R<=i;++R){let P=R/i*Math.PI*2,V=-t*Math.cos(P),X=t*Math.sin(P);p.x=m.x+(V*y.x+X*g.x),p.y=m.y+(V*y.y+X*g.y),p.z=m.z+(V*y.z+X*g.z),c.push(p.x,p.y,p.z),f.subVectors(p,m).normalize(),l.push(f.x,f.y,f.z),h.push(S/n),h.push(R/i)}}for(let S=1;S<=n;S++)for(let E=1;E<=i;E++){let R=(i+1)*(S-1)+(E-1),P=(i+1)*S+(E-1),V=(i+1)*S+E,X=(i+1)*(S-1)+E;o.push(R,P,X),o.push(P,V,X)}function x(S,E,R,P,V){let X=Math.cos(S),H=Math.sin(S),se=R/E*S,oe=Math.cos(se);V.x=P*(2+oe)*.5*X,V.y=P*(2+oe)*H*.5,V.z=P*Math.sin(se)*.5}this.setIndex(o),this.setAttribute("position",new $e(c,3)),this.setAttribute("normal",new $e(l,3)),this.setAttribute("uv",new $e(h,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}static fromJSON(e){return new r(e.radius,e.tube,e.tubularSegments,e.radialSegments,e.p,e.q)}},Dl=class r extends St{constructor(e=new Na(new N(-1,-1,0),new N(-1,1,0),new N(1,1,0)),t=64,n=1,i=8,s=!1){super(),this.type="TubeGeometry",this.parameters={path:e,tubularSegments:t,radius:n,radialSegments:i,closed:s};let a=e.computeFrenetFrames(t,s);this.tangents=a.tangents,this.normals=a.normals,this.binormals=a.binormals;let o=new N,c=new N,l=new Te,h=new N,p=[],f=[],m=[],v=[];function g(u){h=e.getPointAt(u/t,h);let y=a.normals[u],x=a.binormals[u];for(let S=0;S<=i;S++){let E=S/i*Math.PI*2,R=Math.sin(E),P=-Math.cos(E);c.x=P*y.x+R*x.x,c.y=P*y.y+R*x.y,c.z=P*y.z+R*x.z,c.normalize(),f.push(c.x,c.y,c.z),o.x=h.x+n*c.x,o.y=h.y+n*c.y,o.z=h.z+n*c.z,p.push(o.x,o.y,o.z)}}(function(){for(let u=0;u<t;u++)g(u);g(s===!1?t:0),function(){for(let u=0;u<=t;u++)for(let y=0;y<=i;y++)l.x=u/t,l.y=y/i,m.push(l.x,l.y)}(),function(){for(let u=1;u<=t;u++)for(let y=1;y<=i;y++){let x=(i+1)*(u-1)+(y-1),S=(i+1)*u+(y-1),E=(i+1)*u+y,R=(i+1)*(u-1)+y;v.push(x,S,R),v.push(S,E,R)}}()})(),this.setIndex(v),this.setAttribute("position",new $e(p,3)),this.setAttribute("normal",new $e(f,3)),this.setAttribute("uv",new $e(m,2))}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}toJSON(){let e=super.toJSON();return e.path=this.parameters.path.toJSON(),e}static fromJSON(e){return new r(new Ml[e.path.type]().fromJSON(e.path),e.tubularSegments,e.radius,e.radialSegments,e.closed)}},Ul=class extends St{constructor(e=null){if(super(),this.type="WireframeGeometry",this.parameters={geometry:e},e!==null){let t=[],n=new Set,i=new N,s=new N;if(e.index!==null){let a=e.attributes.position,o=e.index,c=e.groups;c.length===0&&(c=[{start:0,count:o.count,materialIndex:0}]);for(let l=0,h=c.length;l<h;++l){let p=c[l],f=p.start;for(let m=f,v=f+p.count;m<v;m+=3)for(let g=0;g<3;g++){let u=o.getX(m+g),y=o.getX(m+(g+1)%3);i.fromBufferAttribute(a,u),s.fromBufferAttribute(a,y),rp(i,s,n)===!0&&(t.push(i.x,i.y,i.z),t.push(s.x,s.y,s.z))}}}else{let a=e.attributes.position;for(let o=0,c=a.count/3;o<c;o++)for(let l=0;l<3;l++){let h=3*o+l,p=3*o+(l+1)%3;i.fromBufferAttribute(a,h),s.fromBufferAttribute(a,p),rp(i,s,n)===!0&&(t.push(i.x,i.y,i.z),t.push(s.x,s.y,s.z))}}this.setAttribute("position",new $e(t,3))}}copy(e){return super.copy(e),this.parameters=Object.assign({},e.parameters),this}};function rp(r,e,t){let n=`${r.x},${r.y},${r.z}-${e.x},${e.y},${e.z}`,i=`${e.x},${e.y},${e.z}-${r.x},${r.y},${r.z}`;return t.has(n)!==!0&&t.has(i)!==!0&&(t.add(n),t.add(i),!0)}var E1=Object.freeze({__proto__:null,BoxGeometry:jn,CapsuleGeometry:pl,CircleGeometry:fl,ConeGeometry:ml,CylinderGeometry:Ia,DodecahedronGeometry:gl,EdgesGeometry:_l,ExtrudeGeometry:Sl,IcosahedronGeometry:Tl,LatheGeometry:El,OctahedronGeometry:wl,PlaneGeometry:vn,PolyhedronGeometry:cr,RingGeometry:Al,ShapeGeometry:Cl,SphereGeometry:Rl,TetrahedronGeometry:Pl,TorusGeometry:Il,TorusKnotGeometry:Ll,TubeGeometry:Dl,WireframeGeometry:Ul});function zr(r){let e={};for(let t in r){e[t]={};for(let n in r[t]){let i=r[t][n];i&&(i.isColor||i.isMatrix3||i.isMatrix4||i.isVector2||i.isVector3||i.isVector4||i.isTexture||i.isQuaternion)?i.isRenderTargetTexture?(tt("UniformsUtils: Textures of render targets cannot be cloned via cloneUniforms() or mergeUniforms()."),e[t][n]=null):e[t][n]=i.clone():Array.isArray(i)?e[t][n]=i.slice():e[t][n]=i}}return e}function yn(r){let e={};for(let t=0;t<r.length;t++){let n=zr(r[t]);for(let i in n)e[i]=n[i]}return e}function Ou(r){let e=r.getRenderTarget();return e===null?r.outputColorSpace:e.isXRRenderTarget===!0?e.texture.colorSpace:Mt.workingColorSpace}var rf={clone:zr,merge:yn},qn=class extends Si{constructor(e){super(),this.isShaderMaterial=!0,this.type="ShaderMaterial",this.defines={},this.uniforms={},this.uniformsGroups=[],this.vertexShader=`void main() {
	gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
}`,this.fragmentShader=`void main() {
	gl_FragColor = vec4( 1.0, 0.0, 0.0, 1.0 );
}`,this.linewidth=1,this.wireframe=!1,this.wireframeLinewidth=1,this.fog=!1,this.lights=!1,this.clipping=!1,this.forceSinglePass=!0,this.extensions={clipCullDistance:!1,multiDraw:!1},this.defaultAttributeValues={color:[1,1,1],uv:[0,0],uv1:[0,0]},this.index0AttributeName=void 0,this.uniformsNeedUpdate=!1,this.glslVersion=null,e!==void 0&&this.setValues(e)}copy(e){return super.copy(e),this.fragmentShader=e.fragmentShader,this.vertexShader=e.vertexShader,this.uniforms=zr(e.uniforms),this.uniformsGroups=function(t){let n=[];for(let i=0;i<t.length;i++)n.push(t[i].clone());return n}(e.uniformsGroups),this.defines=Object.assign({},e.defines),this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this.fog=e.fog,this.lights=e.lights,this.clipping=e.clipping,this.extensions=Object.assign({},e.extensions),this.glslVersion=e.glslVersion,this.defaultAttributeValues=Object.assign({},e.defaultAttributeValues),this.index0AttributeName=e.index0AttributeName,this.uniformsNeedUpdate=e.uniformsNeedUpdate,this}toJSON(e){let t=super.toJSON(e);t.glslVersion=this.glslVersion,t.uniforms={};for(let i in this.uniforms){let s=this.uniforms[i].value;s&&s.isTexture?t.uniforms[i]={type:"t",value:s.toJSON(e).uuid}:s&&s.isColor?t.uniforms[i]={type:"c",value:s.getHex()}:s&&s.isVector2?t.uniforms[i]={type:"v2",value:s.toArray()}:s&&s.isVector3?t.uniforms[i]={type:"v3",value:s.toArray()}:s&&s.isVector4?t.uniforms[i]={type:"v4",value:s.toArray()}:s&&s.isMatrix3?t.uniforms[i]={type:"m3",value:s.toArray()}:s&&s.isMatrix4?t.uniforms[i]={type:"m4",value:s.toArray()}:t.uniforms[i]={value:s}}Object.keys(this.defines).length>0&&(t.defines=this.defines),t.vertexShader=this.vertexShader,t.fragmentShader=this.fragmentShader,t.lights=this.lights,t.clipping=this.clipping;let n={};for(let i in this.extensions)this.extensions[i]===!0&&(n[i]=!0);return Object.keys(n).length>0&&(t.extensions=n),t}},Nl=class extends qn{constructor(e){super(e),this.isRawShaderMaterial=!0,this.type="RawShaderMaterial"}},hn=class extends Si{constructor(e){super(),this.isMeshStandardMaterial=!0,this.type="MeshStandardMaterial",this.defines={STANDARD:""},this.color=new ke(16777215),this.roughness=1,this.metalness=0,this.map=null,this.lightMap=null,this.lightMapIntensity=1,this.aoMap=null,this.aoMapIntensity=1,this.emissive=new ke(0),this.emissiveIntensity=1,this.emissiveMap=null,this.bumpMap=null,this.bumpScale=1,this.normalMap=null,this.normalMapType=0,this.normalScale=new Te(1,1),this.displacementMap=null,this.displacementScale=1,this.displacementBias=0,this.roughnessMap=null,this.metalnessMap=null,this.alphaMap=null,this.envMap=null,this.envMapRotation=new Wn,this.envMapIntensity=1,this.wireframe=!1,this.wireframeLinewidth=1,this.wireframeLinecap="round",this.wireframeLinejoin="round",this.flatShading=!1,this.fog=!0,this.setValues(e)}copy(e){return super.copy(e),this.defines={STANDARD:""},this.color.copy(e.color),this.roughness=e.roughness,this.metalness=e.metalness,this.map=e.map,this.lightMap=e.lightMap,this.lightMapIntensity=e.lightMapIntensity,this.aoMap=e.aoMap,this.aoMapIntensity=e.aoMapIntensity,this.emissive.copy(e.emissive),this.emissiveMap=e.emissiveMap,this.emissiveIntensity=e.emissiveIntensity,this.bumpMap=e.bumpMap,this.bumpScale=e.bumpScale,this.normalMap=e.normalMap,this.normalMapType=e.normalMapType,this.normalScale.copy(e.normalScale),this.displacementMap=e.displacementMap,this.displacementScale=e.displacementScale,this.displacementBias=e.displacementBias,this.roughnessMap=e.roughnessMap,this.metalnessMap=e.metalnessMap,this.alphaMap=e.alphaMap,this.envMap=e.envMap,this.envMapRotation.copy(e.envMapRotation),this.envMapIntensity=e.envMapIntensity,this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this.wireframeLinecap=e.wireframeLinecap,this.wireframeLinejoin=e.wireframeLinejoin,this.flatShading=e.flatShading,this.fog=e.fog,this}};var Ol=class extends Si{constructor(e){super(),this.isMeshDepthMaterial=!0,this.type="MeshDepthMaterial",this.depthPacking=3200,this.map=null,this.alphaMap=null,this.displacementMap=null,this.displacementScale=1,this.displacementBias=0,this.wireframe=!1,this.wireframeLinewidth=1,this.setValues(e)}copy(e){return super.copy(e),this.depthPacking=e.depthPacking,this.map=e.map,this.alphaMap=e.alphaMap,this.displacementMap=e.displacementMap,this.displacementScale=e.displacementScale,this.displacementBias=e.displacementBias,this.wireframe=e.wireframe,this.wireframeLinewidth=e.wireframeLinewidth,this}},Fl=class extends Si{constructor(e){super(),this.isMeshDistanceMaterial=!0,this.type="MeshDistanceMaterial",this.map=null,this.alphaMap=null,this.displacementMap=null,this.displacementScale=1,this.displacementBias=0,this.setValues(e)}copy(e){return super.copy(e),this.map=e.map,this.alphaMap=e.alphaMap,this.displacementMap=e.displacementMap,this.displacementScale=e.displacementScale,this.displacementBias=e.displacementBias,this}};function Ko(r,e){return r&&r.constructor!==e?typeof e.BYTES_PER_ELEMENT=="number"?new e(r):Array.prototype.slice.call(r):r}var hr=class{constructor(e,t,n,i){this.parameterPositions=e,this._cachedIndex=0,this.resultBuffer=i!==void 0?i:new t.constructor(n),this.sampleValues=t,this.valueSize=n,this.settings=null,this.DefaultSettings_={}}evaluate(e){let t=this.parameterPositions,n=this._cachedIndex,i=t[n],s=t[n-1];n:{e:{let a;t:{i:if(!(e<i)){for(let o=n+2;;){if(i===void 0){if(e<s)break i;return n=t.length,this._cachedIndex=n,this.copySampleValue_(n-1)}if(n===o)break;if(s=i,i=t[++n],e<i)break e}a=t.length;break t}if(!(e>=s)){let o=t[1];e<o&&(n=2,s=o);for(let c=n-2;;){if(s===void 0)return this._cachedIndex=0,this.copySampleValue_(0);if(n===c)break;if(i=s,s=t[--n-1],e>=s)break e}a=n,n=0;break t}break n}for(;n<a;){let o=n+a>>>1;e<t[o]?a=o:n=o+1}if(i=t[n],s=t[n-1],s===void 0)return this._cachedIndex=0,this.copySampleValue_(0);if(i===void 0)return n=t.length,this._cachedIndex=n,this.copySampleValue_(n-1)}this._cachedIndex=n,this.intervalChanged_(n,s,i)}return this.interpolate_(n,s,e,i)}getSettings_(){return this.settings||this.DefaultSettings_}copySampleValue_(e){let t=this.resultBuffer,n=this.sampleValues,i=this.valueSize,s=e*i;for(let a=0;a!==i;++a)t[a]=n[s+a];return t}interpolate_(){throw new Error("call to abstract method")}intervalChanged_(){}},Bl=class extends hr{constructor(e,t,n,i){super(e,t,n,i),this._weightPrev=-0,this._offsetPrev=-0,this._weightNext=-0,this._offsetNext=-0,this.DefaultSettings_={endingStart:yh,endingEnd:yh}}intervalChanged_(e,t,n){let i=this.parameterPositions,s=e-2,a=e+1,o=i[s],c=i[a];if(o===void 0)switch(this.getSettings_().endingStart){case xh:s=e,o=2*t-n;break;case Mh:s=i.length-2,o=t+i[s]-i[s+1];break;default:s=e,o=n}if(c===void 0)switch(this.getSettings_().endingEnd){case xh:a=e,c=2*n-t;break;case Mh:a=1,c=n+i[1]-i[0];break;default:a=e-1,c=t}let l=.5*(n-t),h=this.valueSize;this._weightPrev=l/(t-o),this._weightNext=l/(c-n),this._offsetPrev=s*h,this._offsetNext=a*h}interpolate_(e,t,n,i){let s=this.resultBuffer,a=this.sampleValues,o=this.valueSize,c=e*o,l=c-o,h=this._offsetPrev,p=this._offsetNext,f=this._weightPrev,m=this._weightNext,v=(n-t)/(i-t),g=v*v,u=g*v,y=-f*u+2*f*g-f*v,x=(1+f)*u+(-1.5-2*f)*g+(-.5+f)*v+1,S=(-1-m)*u+(1.5+m)*g+.5*v,E=m*u-m*g;for(let R=0;R!==o;++R)s[R]=y*a[h+R]+x*a[l+R]+S*a[c+R]+E*a[p+R];return s}},zl=class extends hr{constructor(e,t,n,i){super(e,t,n,i)}interpolate_(e,t,n,i){let s=this.resultBuffer,a=this.sampleValues,o=this.valueSize,c=e*o,l=c-o,h=(n-t)/(i-t),p=1-h;for(let f=0;f!==o;++f)s[f]=a[l+f]*p+a[c+f]*h;return s}},kl=class extends hr{constructor(e,t,n,i){super(e,t,n,i)}interpolate_(e){return this.copySampleValue_(e-1)}},Hl=class extends hr{interpolate_(e,t,n,i){let s=this.resultBuffer,a=this.sampleValues,o=this.valueSize,c=e*o,l=c-o,h=this.settings||this.DefaultSettings_,p=h.inTangents,f=h.outTangents;if(!p||!f){let g=(n-t)/(i-t),u=1-g;for(let y=0;y!==o;++y)s[y]=a[l+y]*u+a[c+y]*g;return s}let m=2*o,v=e-1;for(let g=0;g!==o;++g){let u=a[l+g],y=a[c+g],x=v*m+2*g,S=f[x],E=f[x+1],R=e*m+2*g,P=p[R],V=p[R+1],X,H,se,oe,te,ne=(n-t)/(i-t);for(let j=0;j<8;j++){X=ne*ne,H=X*ne,se=1-ne,oe=se*se,te=oe*se;let J=te*t+3*oe*ne*S+3*se*X*P+H*i-n;if(Math.abs(J)<1e-10)break;let ie=3*oe*(S-t)+6*se*ne*(P-S)+3*X*(i-P);if(Math.abs(ie)<1e-10)break;ne-=J/ie,ne=Math.max(0,Math.min(1,ne))}s[g]=te*u+3*oe*ne*E+3*se*X*V+H*y}return s}},Gn=class{constructor(e,t,n,i){if(e===void 0)throw new Error("THREE.KeyframeTrack: track name is undefined");if(t===void 0||t.length===0)throw new Error("THREE.KeyframeTrack: no keyframes in track named "+e);this.name=e,this.times=Ko(t,this.TimeBufferType),this.values=Ko(n,this.ValueBufferType),this.setInterpolation(i||this.DefaultInterpolation)}static toJSON(e){let t=e.constructor,n;if(t.toJSON!==this.toJSON)n=t.toJSON(e);else{n={name:e.name,times:Ko(e.times,Array),values:Ko(e.values,Array)};let i=e.getInterpolation();i!==e.DefaultInterpolation&&(n.interpolation=i)}return n.type=e.ValueTypeName,n}InterpolantFactoryMethodDiscrete(e){return new kl(this.times,this.values,this.getValueSize(),e)}InterpolantFactoryMethodLinear(e){return new zl(this.times,this.values,this.getValueSize(),e)}InterpolantFactoryMethodSmooth(e){return new Bl(this.times,this.values,this.getValueSize(),e)}InterpolantFactoryMethodBezier(e){let t=new Hl(this.times,this.values,this.getValueSize(),e);return this.settings&&(t.settings=this.settings),t}setInterpolation(e){let t;switch(e){case _a:t=this.InterpolantFactoryMethodDiscrete;break;case rl:t=this.InterpolantFactoryMethodLinear;break;case el:t=this.InterpolantFactoryMethodSmooth;break;case vh:t=this.InterpolantFactoryMethodBezier}if(t===void 0){let n="unsupported interpolation for "+this.ValueTypeName+" keyframe track named "+this.name;if(this.createInterpolant===void 0){if(e===this.DefaultInterpolation)throw new Error(n);this.setInterpolation(this.DefaultInterpolation)}return tt("KeyframeTrack:",n),this}return this.createInterpolant=t,this}getInterpolation(){switch(this.createInterpolant){case this.InterpolantFactoryMethodDiscrete:return _a;case this.InterpolantFactoryMethodLinear:return rl;case this.InterpolantFactoryMethodSmooth:return el;case this.InterpolantFactoryMethodBezier:return vh}}getValueSize(){return this.values.length/this.times.length}shift(e){if(e!==0){let t=this.times;for(let n=0,i=t.length;n!==i;++n)t[n]+=e}return this}scale(e){if(e!==1){let t=this.times;for(let n=0,i=t.length;n!==i;++n)t[n]*=e}return this}trim(e,t){let n=this.times,i=n.length,s=0,a=i-1;for(;s!==i&&n[s]<e;)++s;for(;a!==-1&&n[a]>t;)--a;if(++a,s!==0||a!==i){s>=a&&(a=Math.max(a,1),s=a-1);let o=this.getValueSize();this.times=n.slice(s,a),this.values=this.values.slice(s*o,a*o)}return this}validate(){let e=!0,t=this.getValueSize();t-Math.floor(t)!==0&&(rt("KeyframeTrack: Invalid value size in track.",this),e=!1);let n=this.times,i=this.values,s=n.length;s===0&&(rt("KeyframeTrack: Track is empty.",this),e=!1);let a=null;for(let o=0;o!==s;o++){let c=n[o];if(typeof c=="number"&&isNaN(c)){rt("KeyframeTrack: Time is not a valid number.",this,o,c),e=!1;break}if(a!==null&&a>c){rt("KeyframeTrack: Out of order keys.",this,o,c,a),e=!1;break}a=c}if(i!==void 0&&cm(i))for(let o=0,c=i.length;o!==c;++o){let l=i[o];if(isNaN(l)){rt("KeyframeTrack: Value is not a valid number.",this,o,l),e=!1;break}}return e}optimize(){let e=this.times.slice(),t=this.values.slice(),n=this.getValueSize(),i=this.getInterpolation()===el,s=e.length-1,a=1;for(let o=1;o<s;++o){let c=!1,l=e[o];if(l!==e[o+1]&&(o!==1||l!==e[0]))if(i)c=!0;else{let h=o*n,p=h-n,f=h+n;for(let m=0;m!==n;++m){let v=t[h+m];if(v!==t[p+m]||v!==t[f+m]){c=!0;break}}}if(c){if(o!==a){e[a]=e[o];let h=o*n,p=a*n;for(let f=0;f!==n;++f)t[p+f]=t[h+f]}++a}}if(s>0){e[a]=e[s];for(let o=s*n,c=a*n,l=0;l!==n;++l)t[c+l]=t[o+l];++a}return a!==e.length?(this.times=e.slice(0,a),this.values=t.slice(0,a*n)):(this.times=e,this.values=t),this}clone(){let e=this.times.slice(),t=this.values.slice(),n=new this.constructor(this.name,e,t);return n.createInterpolant=this.createInterpolant,n}};Gn.prototype.ValueTypeName="",Gn.prototype.TimeBufferType=Float32Array,Gn.prototype.ValueBufferType=Float32Array,Gn.prototype.DefaultInterpolation=rl;var rr=class extends Gn{constructor(e,t,n){super(e,t,n)}};rr.prototype.ValueTypeName="bool",rr.prototype.ValueBufferType=Array,rr.prototype.DefaultInterpolation=_a,rr.prototype.InterpolantFactoryMethodLinear=void 0,rr.prototype.InterpolantFactoryMethodSmooth=void 0;var Gl=class extends Gn{constructor(e,t,n,i){super(e,t,n,i)}};Gl.prototype.ValueTypeName="color";var Vl=class extends Gn{constructor(e,t,n,i){super(e,t,n,i)}};Vl.prototype.ValueTypeName="number";var Wl=class extends hr{constructor(e,t,n,i){super(e,t,n,i)}interpolate_(e,t,n,i){let s=this.resultBuffer,a=this.sampleValues,o=this.valueSize,c=(n-t)/(i-t),l=e*o;for(let h=l+o;l!==h;l+=4)Bt.slerpFlat(s,0,a,l-o,a,l,c);return s}},Ga=class extends Gn{constructor(e,t,n,i){super(e,t,n,i)}InterpolantFactoryMethodLinear(e){return new Wl(this.times,this.values,this.getValueSize(),e)}};Ga.prototype.ValueTypeName="quaternion",Ga.prototype.InterpolantFactoryMethodSmooth=void 0;var sr=class extends Gn{constructor(e,t,n){super(e,t,n)}};sr.prototype.ValueTypeName="string",sr.prototype.ValueBufferType=Array,sr.prototype.DefaultInterpolation=_a,sr.prototype.InterpolantFactoryMethodLinear=void 0,sr.prototype.InterpolantFactoryMethodSmooth=void 0;var jl=class extends Gn{constructor(e,t,n,i){super(e,t,n,i)}};jl.prototype.ValueTypeName="vector";var tl={enabled:!1,files:{},add:function(r,e){this.enabled!==!1&&(sp(r)||(this.files[r]=e))},get:function(r){if(this.enabled!==!1&&!sp(r))return this.files[r]},remove:function(r){delete this.files[r]},clear:function(){this.files={}}};function sp(r){try{let e=r.slice(r.indexOf(":")+1);return new URL(e).protocol==="blob:"}catch{return!1}}var Xl=class{constructor(e,t,n){let i=this,s,a=!1,o=0,c=0,l=[];this.onStart=void 0,this.onLoad=e,this.onProgress=t,this.onError=n,this._abortController=null,this.itemStart=function(h){c++,a===!1&&i.onStart!==void 0&&i.onStart(h,o,c),a=!0},this.itemEnd=function(h){o++,i.onProgress!==void 0&&i.onProgress(h,o,c),o===c&&(a=!1,i.onLoad!==void 0&&i.onLoad())},this.itemError=function(h){i.onError!==void 0&&i.onError(h)},this.resolveURL=function(h){return s?s(h):h},this.setURLModifier=function(h){return s=h,this},this.addHandler=function(h,p){return l.push(h,p),this},this.removeHandler=function(h){let p=l.indexOf(h);return p!==-1&&l.splice(p,2),this},this.getHandler=function(h){for(let p=0,f=l.length;p<f;p+=2){let m=l[p],v=l[p+1];if(m.global&&(m.lastIndex=0),m.test(h))return v}return null},this.abort=function(){return this.abortController.abort(),this._abortController=null,this}}get abortController(){return this._abortController||(this._abortController=new AbortController),this._abortController}},sf=new Xl,As=class{constructor(e){this.manager=e!==void 0?e:sf,this.crossOrigin="anonymous",this.withCredentials=!1,this.path="",this.resourcePath="",this.requestHeader={},typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("observe",{detail:this}))}load(){}loadAsync(e,t){let n=this;return new Promise(function(i,s){n.load(e,i,t,s)})}parse(){}setCrossOrigin(e){return this.crossOrigin=e,this}setWithCredentials(e){return this.withCredentials=e,this}setPath(e){return this.path=e,this}setResourcePath(e){return this.resourcePath=e,this}setRequestHeader(e){return this.requestHeader=e,this}abort(){return this}};As.DEFAULT_MATERIAL_NAME="__DEFAULT";var hs=new WeakMap,ql=class extends As{constructor(e){super(e)}load(e,t,n,i){this.path!==void 0&&(e=this.path+e),e=this.manager.resolveURL(e);let s=this,a=tl.get(`image:${e}`);if(a!==void 0){if(a.complete===!0)s.manager.itemStart(e),setTimeout(function(){t&&t(a),s.manager.itemEnd(e)},0);else{let p=hs.get(a);p===void 0&&(p=[],hs.set(a,p)),p.push({onLoad:t,onError:i})}return a}let o=_s("img");function c(){h(),t&&t(this);let p=hs.get(this)||[];for(let f=0;f<p.length;f++){let m=p[f];m.onLoad&&m.onLoad(this)}hs.delete(this),s.manager.itemEnd(e)}function l(p){h(),i&&i(p),tl.remove(`image:${e}`);let f=hs.get(this)||[];for(let m=0;m<f.length;m++){let v=f[m];v.onError&&v.onError(p)}hs.delete(this),s.manager.itemError(e),s.manager.itemEnd(e)}function h(){o.removeEventListener("load",c,!1),o.removeEventListener("error",l,!1)}return o.addEventListener("load",c,!1),o.addEventListener("error",l,!1),e.slice(0,5)!=="data:"&&this.crossOrigin!==void 0&&(o.crossOrigin=this.crossOrigin),tl.add(`image:${e}`,o),s.manager.itemStart(e),o.src=e,o}};var Va=class extends As{constructor(e){super(e)}load(e,t,n,i){let s=new _n,a=new ql(this.manager);return a.setCrossOrigin(this.crossOrigin),a.setPath(this.path),a.load(e,function(o){s.image=o,s.needsUpdate=!0,t!==void 0&&t(s)},n,i),s}},Cs=class extends Gt{constructor(e,t=1){super(),this.isLight=!0,this.type="Light",this.color=new ke(e),this.intensity=t}dispose(){this.dispatchEvent({type:"dispose"})}copy(e,t){return super.copy(e,t),this.color.copy(e.color),this.intensity=e.intensity,this}toJSON(e){let t=super.toJSON(e);return t.object.color=this.color.getHex(),t.object.intensity=this.intensity,t}},Wa=class extends Cs{constructor(e,t,n){super(e,n),this.isHemisphereLight=!0,this.type="HemisphereLight",this.position.copy(Gt.DEFAULT_UP),this.updateMatrix(),this.groundColor=new ke(t)}copy(e,t){return super.copy(e,t),this.groundColor.copy(e.groundColor),this}toJSON(e){let t=super.toJSON(e);return t.object.groundColor=this.groundColor.getHex(),t}},gh=new st,ap=new N,op=new N,Yl=class{constructor(e){this.camera=e,this.intensity=1,this.bias=0,this.biasNode=null,this.normalBias=0,this.radius=1,this.blurSamples=8,this.mapSize=new Te(512,512),this.mapType=Yn,this.map=null,this.mapPass=null,this.matrix=new st,this.autoUpdate=!0,this.needsUpdate=!1,this._frustum=new or,this._frameExtents=new Te(1,1),this._viewportCount=1,this._viewports=[new Lt(0,0,1,1)]}getViewportCount(){return this._viewportCount}getFrustum(){return this._frustum}updateMatrices(e){let t=this.camera,n=this.matrix;ap.setFromMatrixPosition(e.matrixWorld),t.position.copy(ap),op.setFromMatrixPosition(e.target.matrixWorld),t.lookAt(op),t.updateMatrixWorld(),gh.multiplyMatrices(t.projectionMatrix,t.matrixWorldInverse),this._frustum.setFromProjectionMatrix(gh,t.coordinateSystem,t.reversedDepth),t.coordinateSystem===gs||t.reversedDepth?n.set(.5,0,0,.5,0,.5,0,.5,0,0,1,0,0,0,0,1):n.set(.5,0,0,.5,0,.5,0,.5,0,0,.5,.5,0,0,0,1),n.multiply(gh)}getViewport(e){return this._viewports[e]}getFrameExtents(){return this._frameExtents}dispose(){this.map&&this.map.dispose(),this.mapPass&&this.mapPass.dispose()}copy(e){return this.camera=e.camera.clone(),this.intensity=e.intensity,this.bias=e.bias,this.radius=e.radius,this.autoUpdate=e.autoUpdate,this.needsUpdate=e.needsUpdate,this.normalBias=e.normalBias,this.blurSamples=e.blurSamples,this.mapSize.copy(e.mapSize),this.biasNode=e.biasNode,this}clone(){return new this.constructor().copy(this)}toJSON(){let e={};return this.intensity!==1&&(e.intensity=this.intensity),this.bias!==0&&(e.bias=this.bias),this.normalBias!==0&&(e.normalBias=this.normalBias),this.radius!==1&&(e.radius=this.radius),this.mapSize.x===512&&this.mapSize.y===512||(e.mapSize=this.mapSize.toArray()),e.camera=this.camera.toJSON(!1).object,delete e.camera.matrix,e}},$o=new N,Qo=new Bt,xi=new N,Rs=class extends Gt{constructor(){super(),this.isCamera=!0,this.type="Camera",this.matrixWorldInverse=new st,this.projectionMatrix=new st,this.projectionMatrixInverse=new st,this.coordinateSystem=Xi,this._reversedDepth=!1}get reversedDepth(){return this._reversedDepth}copy(e,t){return super.copy(e,t),this.matrixWorldInverse.copy(e.matrixWorldInverse),this.projectionMatrix.copy(e.projectionMatrix),this.projectionMatrixInverse.copy(e.projectionMatrixInverse),this.coordinateSystem=e.coordinateSystem,this}getWorldDirection(e){return super.getWorldDirection(e).negate()}updateMatrixWorld(e){super.updateMatrixWorld(e),this.matrixWorld.decompose($o,Qo,xi),xi.x===1&&xi.y===1&&xi.z===1?this.matrixWorldInverse.copy(this.matrixWorld).invert():this.matrixWorldInverse.compose($o,Qo,xi.set(1,1,1)).invert()}updateWorldMatrix(e,t){super.updateWorldMatrix(e,t),this.matrixWorld.decompose($o,Qo,xi),xi.x===1&&xi.y===1&&xi.z===1?this.matrixWorldInverse.copy(this.matrixWorld).invert():this.matrixWorldInverse.compose($o,Qo,xi.set(1,1,1)).invert()}clone(){return new this.constructor().copy(this)}},ir=new N,lp=new Te,cp=new Te,an=class extends Rs{constructor(e=50,t=1,n=.1,i=2e3){super(),this.isPerspectiveCamera=!0,this.type="PerspectiveCamera",this.fov=e,this.zoom=1,this.near=n,this.far=i,this.focus=10,this.aspect=t,this.view=null,this.filmGauge=35,this.filmOffset=0,this.updateProjectionMatrix()}copy(e,t){return super.copy(e,t),this.fov=e.fov,this.zoom=e.zoom,this.near=e.near,this.far=e.far,this.focus=e.focus,this.aspect=e.aspect,this.view=e.view===null?null:Object.assign({},e.view),this.filmGauge=e.filmGauge,this.filmOffset=e.filmOffset,this}setFocalLength(e){let t=.5*this.getFilmHeight()/e;this.fov=2*ys*Math.atan(t),this.updateProjectionMatrix()}getFocalLength(){let e=Math.tan(.5*ps*this.fov);return .5*this.getFilmHeight()/e}getEffectiveFOV(){return 2*ys*Math.atan(Math.tan(.5*ps*this.fov)/this.zoom)}getFilmWidth(){return this.filmGauge*Math.min(this.aspect,1)}getFilmHeight(){return this.filmGauge/Math.max(this.aspect,1)}getViewBounds(e,t,n){ir.set(-1,-1,.5).applyMatrix4(this.projectionMatrixInverse),t.set(ir.x,ir.y).multiplyScalar(-e/ir.z),ir.set(1,1,.5).applyMatrix4(this.projectionMatrixInverse),n.set(ir.x,ir.y).multiplyScalar(-e/ir.z)}getViewSize(e,t){return this.getViewBounds(e,lp,cp),t.subVectors(cp,lp)}setViewOffset(e,t,n,i,s,a){this.aspect=e/t,this.view===null&&(this.view={enabled:!0,fullWidth:1,fullHeight:1,offsetX:0,offsetY:0,width:1,height:1}),this.view.enabled=!0,this.view.fullWidth=e,this.view.fullHeight=t,this.view.offsetX=n,this.view.offsetY=i,this.view.width=s,this.view.height=a,this.updateProjectionMatrix()}clearViewOffset(){this.view!==null&&(this.view.enabled=!1),this.updateProjectionMatrix()}updateProjectionMatrix(){let e=this.near,t=e*Math.tan(.5*ps*this.fov)/this.zoom,n=2*t,i=this.aspect*n,s=-.5*i,a=this.view;if(this.view!==null&&this.view.enabled){let c=a.fullWidth,l=a.fullHeight;s+=a.offsetX*i/c,t-=a.offsetY*n/l,i*=a.width/c,n*=a.height/l}let o=this.filmOffset;o!==0&&(s+=e*o/this.getFilmWidth()),this.projectionMatrix.makePerspective(s,s+i,t,t-n,e,this.far,this.coordinateSystem,this.reversedDepth),this.projectionMatrixInverse.copy(this.projectionMatrix).invert()}toJSON(e){let t=super.toJSON(e);return t.object.fov=this.fov,t.object.zoom=this.zoom,t.object.near=this.near,t.object.far=this.far,t.object.focus=this.focus,t.object.aspect=this.aspect,this.view!==null&&(t.object.view=Object.assign({},this.view)),t.object.filmGauge=this.filmGauge,t.object.filmOffset=this.filmOffset,t}};var Rh=class extends Yl{constructor(){super(new an(90,1,.5,500)),this.isPointLightShadow=!0}},Ps=class extends Cs{constructor(e,t,n=0,i=2){super(e,t),this.isPointLight=!0,this.type="PointLight",this.distance=n,this.decay=i,this.shadow=new Rh}get power(){return 4*this.intensity*Math.PI}set power(e){this.intensity=e/(4*Math.PI)}dispose(){super.dispose(),this.shadow.dispose()}copy(e,t){return super.copy(e,t),this.distance=e.distance,this.decay=e.decay,this.shadow=e.shadow.clone(),this}toJSON(e){let t=super.toJSON(e);return t.object.distance=this.distance,t.object.decay=this.decay,t.object.shadow=this.shadow.toJSON(),t}},Is=class extends Rs{constructor(e=-1,t=1,n=1,i=-1,s=.1,a=2e3){super(),this.isOrthographicCamera=!0,this.type="OrthographicCamera",this.zoom=1,this.view=null,this.left=e,this.right=t,this.top=n,this.bottom=i,this.near=s,this.far=a,this.updateProjectionMatrix()}copy(e,t){return super.copy(e,t),this.left=e.left,this.right=e.right,this.top=e.top,this.bottom=e.bottom,this.near=e.near,this.far=e.far,this.zoom=e.zoom,this.view=e.view===null?null:Object.assign({},e.view),this}setViewOffset(e,t,n,i,s,a){this.view===null&&(this.view={enabled:!0,fullWidth:1,fullHeight:1,offsetX:0,offsetY:0,width:1,height:1}),this.view.enabled=!0,this.view.fullWidth=e,this.view.fullHeight=t,this.view.offsetX=n,this.view.offsetY=i,this.view.width=s,this.view.height=a,this.updateProjectionMatrix()}clearViewOffset(){this.view!==null&&(this.view.enabled=!1),this.updateProjectionMatrix()}updateProjectionMatrix(){let e=(this.right-this.left)/(2*this.zoom),t=(this.top-this.bottom)/(2*this.zoom),n=(this.right+this.left)/2,i=(this.top+this.bottom)/2,s=n-e,a=n+e,o=i+t,c=i-t;if(this.view!==null&&this.view.enabled){let l=(this.right-this.left)/this.view.fullWidth/this.zoom,h=(this.top-this.bottom)/this.view.fullHeight/this.zoom;s+=l*this.view.offsetX,a=s+l*this.view.width,o-=h*this.view.offsetY,c=o-h*this.view.height}this.projectionMatrix.makeOrthographic(s,a,o,c,this.near,this.far,this.coordinateSystem,this.reversedDepth),this.projectionMatrixInverse.copy(this.projectionMatrix).invert()}toJSON(e){let t=super.toJSON(e);return t.object.zoom=this.zoom,t.object.left=this.left,t.object.right=this.right,t.object.top=this.top,t.object.bottom=this.bottom,t.object.near=this.near,t.object.far=this.far,this.view!==null&&(t.object.view=Object.assign({},this.view)),t}},Ph=class extends Yl{constructor(){super(new Is(-5,5,5,-5,.5,500)),this.isDirectionalLightShadow=!0}},Lr=class extends Cs{constructor(e,t){super(e,t),this.isDirectionalLight=!0,this.type="DirectionalLight",this.position.copy(Gt.DEFAULT_UP),this.updateMatrix(),this.target=new Gt,this.shadow=new Ph}dispose(){super.dispose(),this.shadow.dispose()}copy(e){return super.copy(e),this.target=e.target.clone(),this.shadow=e.shadow.clone(),this}toJSON(e){let t=super.toJSON(e);return t.object.shadow=this.shadow.toJSON(),t.object.target=this.target.uuid,t}};var w1=new st,A1=new st,C1=new st;var us=-90,Zl=class extends Gt{constructor(e,t,n){super(),this.type="CubeCamera",this.renderTarget=n,this.coordinateSystem=null,this.activeMipmapLevel=0;let i=new an(us,1,e,t);i.layers=this.layers,this.add(i);let s=new an(us,1,e,t);s.layers=this.layers,this.add(s);let a=new an(us,1,e,t);a.layers=this.layers,this.add(a);let o=new an(us,1,e,t);o.layers=this.layers,this.add(o);let c=new an(us,1,e,t);c.layers=this.layers,this.add(c);let l=new an(us,1,e,t);l.layers=this.layers,this.add(l)}updateCoordinateSystem(){let e=this.coordinateSystem,t=this.children.concat(),[n,i,s,a,o,c]=t;for(let l of t)this.remove(l);if(e===Xi)n.up.set(0,1,0),n.lookAt(1,0,0),i.up.set(0,1,0),i.lookAt(-1,0,0),s.up.set(0,0,-1),s.lookAt(0,1,0),a.up.set(0,0,1),a.lookAt(0,-1,0),o.up.set(0,1,0),o.lookAt(0,0,1),c.up.set(0,1,0),c.lookAt(0,0,-1);else{if(e!==gs)throw new Error("THREE.CubeCamera.updateCoordinateSystem(): Invalid coordinate system: "+e);n.up.set(0,-1,0),n.lookAt(-1,0,0),i.up.set(0,-1,0),i.lookAt(1,0,0),s.up.set(0,0,1),s.lookAt(0,1,0),a.up.set(0,0,-1),a.lookAt(0,-1,0),o.up.set(0,-1,0),o.lookAt(0,0,1),c.up.set(0,-1,0),c.lookAt(0,0,-1)}for(let l of t)this.add(l),l.updateMatrixWorld()}update(e,t){this.parent===null&&this.updateMatrixWorld();let{renderTarget:n,activeMipmapLevel:i}=this;this.coordinateSystem!==e.coordinateSystem&&(this.coordinateSystem=e.coordinateSystem,this.updateCoordinateSystem());let[s,a,o,c,l,h]=this.children,p=e.getRenderTarget(),f=e.getActiveCubeFace(),m=e.getActiveMipmapLevel(),v=e.xr.enabled;e.xr.enabled=!1;let g=n.texture.generateMipmaps;n.texture.generateMipmaps=!1;let u=!1;u=e.isWebGLRenderer===!0?e.state.buffers.depth.getReversed():e.reversedDepthBuffer,e.setRenderTarget(n,0,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,s),e.setRenderTarget(n,1,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,a),e.setRenderTarget(n,2,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,o),e.setRenderTarget(n,3,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,c),e.setRenderTarget(n,4,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,l),n.texture.generateMipmaps=g,e.setRenderTarget(n,5,i),u&&e.autoClear===!1&&e.clearDepth(),e.render(t,h),e.setRenderTarget(p,f,m),e.xr.enabled=v,n.texture.needsPMREMUpdate=!0}},Jl=class extends an{constructor(e=[]){super(),this.isArrayCamera=!0,this.isMultiViewCamera=!1,this.cameras=e}};var R1=new N,P1=new Bt,I1=new N,L1=new N,D1=new N;var U1=new N,N1=new Bt,O1=new N,F1=new N;var Fu="\\[\\]\\.:\\/",km=new RegExp("["+Fu+"]","g"),_h="[^"+Fu+"]",Hm="[^"+Fu.replace("\\.","")+"]",Gm=new RegExp("^"+/((?:WC+[\/:])*)/.source.replace("WC",_h)+/(WCOD+)?/.source.replace("WCOD",Hm)+/(?:\.(WC+)(?:\[(.+)\])?)?/.source.replace("WC",_h)+/\.(WC+)(?:\[(.+)\])?/.source.replace("WC",_h)+"$"),Vm=["material","materials","bones","map"],It=class r{constructor(e,t,n){this.path=t,this.parsedPath=n||r.parseTrackName(t),this.node=r.findNode(e,this.parsedPath.nodeName),this.rootNode=e,this.getValue=this._getValue_unbound,this.setValue=this._setValue_unbound}static create(e,t,n){return e&&e.isAnimationObjectGroup?new r.Composite(e,t,n):new r(e,t,n)}static sanitizeNodeName(e){return e.replace(/\s/g,"_").replace(km,"")}static parseTrackName(e){let t=Gm.exec(e);if(t===null)throw new Error("PropertyBinding: Cannot parse trackName: "+e);let n={nodeName:t[2],objectName:t[3],objectIndex:t[4],propertyName:t[5],propertyIndex:t[6]},i=n.nodeName&&n.nodeName.lastIndexOf(".");if(i!==void 0&&i!==-1){let s=n.nodeName.substring(i+1);Vm.indexOf(s)!==-1&&(n.nodeName=n.nodeName.substring(0,i),n.objectName=s)}if(n.propertyName===null||n.propertyName.length===0)throw new Error("PropertyBinding: can not parse propertyName from trackName: "+e);return n}static findNode(e,t){if(t===void 0||t===""||t==="."||t===-1||t===e.name||t===e.uuid)return e;if(e.skeleton){let n=e.skeleton.getBoneByName(t);if(n!==void 0)return n}if(e.children){let n=function(s){for(let a=0;a<s.length;a++){let o=s[a];if(o.name===t||o.uuid===t)return o;let c=n(o.children);if(c)return c}return null},i=n(e.children);if(i)return i}return null}_getValue_unavailable(){}_setValue_unavailable(){}_getValue_direct(e,t){e[t]=this.targetObject[this.propertyName]}_getValue_array(e,t){let n=this.resolvedProperty;for(let i=0,s=n.length;i!==s;++i)e[t++]=n[i]}_getValue_arrayElement(e,t){e[t]=this.resolvedProperty[this.propertyIndex]}_getValue_toArray(e,t){this.resolvedProperty.toArray(e,t)}_setValue_direct(e,t){this.targetObject[this.propertyName]=e[t]}_setValue_direct_setNeedsUpdate(e,t){this.targetObject[this.propertyName]=e[t],this.targetObject.needsUpdate=!0}_setValue_direct_setMatrixWorldNeedsUpdate(e,t){this.targetObject[this.propertyName]=e[t],this.targetObject.matrixWorldNeedsUpdate=!0}_setValue_array(e,t){let n=this.resolvedProperty;for(let i=0,s=n.length;i!==s;++i)n[i]=e[t++]}_setValue_array_setNeedsUpdate(e,t){let n=this.resolvedProperty;for(let i=0,s=n.length;i!==s;++i)n[i]=e[t++];this.targetObject.needsUpdate=!0}_setValue_array_setMatrixWorldNeedsUpdate(e,t){let n=this.resolvedProperty;for(let i=0,s=n.length;i!==s;++i)n[i]=e[t++];this.targetObject.matrixWorldNeedsUpdate=!0}_setValue_arrayElement(e,t){this.resolvedProperty[this.propertyIndex]=e[t]}_setValue_arrayElement_setNeedsUpdate(e,t){this.resolvedProperty[this.propertyIndex]=e[t],this.targetObject.needsUpdate=!0}_setValue_arrayElement_setMatrixWorldNeedsUpdate(e,t){this.resolvedProperty[this.propertyIndex]=e[t],this.targetObject.matrixWorldNeedsUpdate=!0}_setValue_fromArray(e,t){this.resolvedProperty.fromArray(e,t)}_setValue_fromArray_setNeedsUpdate(e,t){this.resolvedProperty.fromArray(e,t),this.targetObject.needsUpdate=!0}_setValue_fromArray_setMatrixWorldNeedsUpdate(e,t){this.resolvedProperty.fromArray(e,t),this.targetObject.matrixWorldNeedsUpdate=!0}_getValue_unbound(e,t){this.bind(),this.getValue(e,t)}_setValue_unbound(e,t){this.bind(),this.setValue(e,t)}bind(){let e=this.node,t=this.parsedPath,n=t.objectName,i=t.propertyName,s=t.propertyIndex;if(e||(e=r.findNode(this.rootNode,t.nodeName),this.node=e),this.getValue=this._getValue_unavailable,this.setValue=this._setValue_unavailable,!e)return void tt("PropertyBinding: No target node found for track: "+this.path+".");if(n){let l=t.objectIndex;switch(n){case"materials":if(!e.material)return void rt("PropertyBinding: Can not bind to material as node does not have a material.",this);if(!e.material.materials)return void rt("PropertyBinding: Can not bind to material.materials as node.material does not have a materials array.",this);e=e.material.materials;break;case"bones":if(!e.skeleton)return void rt("PropertyBinding: Can not bind to bones as node does not have a skeleton.",this);e=e.skeleton.bones;for(let h=0;h<e.length;h++)if(e[h].name===l){l=h;break}break;case"map":if("map"in e){e=e.map;break}if(!e.material)return void rt("PropertyBinding: Can not bind to material as node does not have a material.",this);if(!e.material.map)return void rt("PropertyBinding: Can not bind to material.map as node.material does not have a map.",this);e=e.material.map;break;default:if(e[n]===void 0)return void rt("PropertyBinding: Can not bind to objectName of node undefined.",this);e=e[n]}if(l!==void 0){if(e[l]===void 0)return void rt("PropertyBinding: Trying to bind to objectIndex of objectName, but is undefined.",this,e);e=e[l]}}let a=e[i];if(a===void 0)return void rt("PropertyBinding: Trying to update property for track: "+t.nodeName+"."+i+" but it wasn't found.",e);let o=this.Versioning.None;this.targetObject=e,e.isMaterial===!0?o=this.Versioning.NeedsUpdate:e.isObject3D===!0&&(o=this.Versioning.MatrixWorldNeedsUpdate);let c=this.BindingType.Direct;if(s!==void 0){if(i==="morphTargetInfluences"){if(!e.geometry)return void rt("PropertyBinding: Can not bind to morphTargetInfluences because node does not have a geometry.",this);if(!e.geometry.morphAttributes)return void rt("PropertyBinding: Can not bind to morphTargetInfluences because node does not have a geometry.morphAttributes.",this);e.morphTargetDictionary[s]!==void 0&&(s=e.morphTargetDictionary[s])}c=this.BindingType.ArrayElement,this.resolvedProperty=a,this.propertyIndex=s}else a.fromArray!==void 0&&a.toArray!==void 0?(c=this.BindingType.HasFromToArray,this.resolvedProperty=a):Array.isArray(a)?(c=this.BindingType.EntireArray,this.resolvedProperty=a):this.propertyName=i;this.getValue=this.GetterByBindingType[c],this.setValue=this.SetterByBindingTypeAndVersioning[c][o]}unbind(){this.node=null,this.getValue=this._getValue_unbound,this.setValue=this._setValue_unbound}};It.Composite=class{constructor(r,e,t){let n=t||It.parseTrackName(e);this._targetGroup=r,this._bindings=r.subscribe_(e,n)}getValue(r,e){this.bind();let t=this._targetGroup.nCachedObjects_,n=this._bindings[t];n!==void 0&&n.getValue(r,e)}setValue(r,e){let t=this._bindings;for(let n=this._targetGroup.nCachedObjects_,i=t.length;n!==i;++n)t[n].setValue(r,e)}bind(){let r=this._bindings;for(let e=this._targetGroup.nCachedObjects_,t=r.length;e!==t;++e)r[e].bind()}unbind(){let r=this._bindings;for(let e=this._targetGroup.nCachedObjects_,t=r.length;e!==t;++e)r[e].unbind()}},It.prototype.BindingType={Direct:0,EntireArray:1,ArrayElement:2,HasFromToArray:3},It.prototype.Versioning={None:0,NeedsUpdate:1,MatrixWorldNeedsUpdate:2},It.prototype.GetterByBindingType=[It.prototype._getValue_direct,It.prototype._getValue_array,It.prototype._getValue_arrayElement,It.prototype._getValue_toArray],It.prototype.SetterByBindingTypeAndVersioning=[[It.prototype._setValue_direct,It.prototype._setValue_direct_setNeedsUpdate,It.prototype._setValue_direct_setMatrixWorldNeedsUpdate],[It.prototype._setValue_array,It.prototype._setValue_array_setNeedsUpdate,It.prototype._setValue_array_setMatrixWorldNeedsUpdate],[It.prototype._setValue_arrayElement,It.prototype._setValue_arrayElement_setNeedsUpdate,It.prototype._setValue_arrayElement_setMatrixWorldNeedsUpdate],[It.prototype._setValue_fromArray,It.prototype._setValue_fromArray_setNeedsUpdate,It.prototype._setValue_fromArray_setMatrixWorldNeedsUpdate]];var B1=new Float32Array(1);var hp=new st,Ls=class{constructor(e,t,n=0,i=1/0){this.ray=new Ti(e,t),this.near=n,this.far=i,this.camera=null,this.layers=new Ms,this.params={Mesh:{},Line:{threshold:1},LOD:{},Points:{threshold:1},Sprite:{}}}set(e,t){this.ray.set(e,t)}setFromCamera(e,t){t.isPerspectiveCamera?(this.ray.origin.setFromMatrixPosition(t.matrixWorld),this.ray.direction.set(e.x,e.y,.5).unproject(t).sub(this.ray.origin).normalize(),this.camera=t):t.isOrthographicCamera?(this.ray.origin.set(e.x,e.y,(t.near+t.far)/(t.near-t.far)).unproject(t),this.ray.direction.set(0,0,-1).transformDirection(t.matrixWorld),this.camera=t):rt("Raycaster: Unsupported camera type: "+t.type)}setFromXRController(e){return hp.identity().extractRotation(e.matrixWorld),this.ray.origin.setFromMatrixPosition(e.matrixWorld),this.ray.direction.set(0,0,-1).applyMatrix4(hp),this}intersectObject(e,t=!0,n=[]){return Ih(e,this,n,t),n.sort(up),n}intersectObjects(e,t=!0,n=[]){for(let i=0,s=e.length;i<s;i++)Ih(e[i],this,n,t);return n.sort(up),n}};function up(r,e){return r.distance-e.distance}function Ih(r,e,t,n){let i=!0;if(r.layers.test(e.layers)&&r.raycast(e,t)===!1&&(i=!1),i===!0&&n===!0){let s=r.children;for(let a=0,o=s.length;a<o;a++)Ih(s[a],e,t,!0)}}var Ds=class{constructor(e=1,t=0,n=0){this.radius=e,this.phi=t,this.theta=n}set(e,t,n){return this.radius=e,this.phi=t,this.theta=n,this}copy(e){return this.radius=e.radius,this.phi=e.phi,this.theta=e.theta,this}makeSafe(){return this.phi=ut(this.phi,1e-6,Math.PI-1e-6),this}setFromVector3(e){return this.setFromCartesianCoords(e.x,e.y,e.z)}setFromCartesianCoords(e,t,n){return this.radius=Math.sqrt(e*e+t*t+n*n),this.radius===0?(this.theta=0,this.phi=0):(this.theta=Math.atan2(e,n),this.phi=Math.acos(ut(t/this.radius,-1,1))),this}clone(){return new this.constructor().copy(this)}};var z1=new Te;var k1=new N,H1=new N,G1=new N,V1=new N,W1=new N,j1=new N,X1=new N;var q1=new N;var Y1=new N,Z1=new st,J1=new st;var K1=new N,$1=new ke,Q1=new ke;var ja=class extends Aa{constructor(e=10,t=10,n=4473924,i=8947848){n=new ke(n),i=new ke(i);let s=t/2,a=e/t,o=e/2,c=[],l=[];for(let p=0,f=0,m=-o;p<=t;p++,m+=a){c.push(-o,0,m,o,0,m),c.push(m,0,-o,m,0,o);let v=p===s?n:i;v.toArray(l,f),f+=3,v.toArray(l,f),f+=3,v.toArray(l,f),f+=3,v.toArray(l,f),f+=3}let h=new St;h.setAttribute("position",new $e(c,3)),h.setAttribute("color",new $e(l,3)),super(h,new Ss({vertexColors:!0,toneMapped:!1})),this.type="GridHelper"}dispose(){this.geometry.dispose(),this.material.dispose()}};var e_=new N,t_=new N,n_=new N;var i_=new N,r_=new Rs;var s_=new ln;var Xa=class extends Aa{constructor(e,t=16776960){let n=new Uint16Array([0,1,1,2,2,3,3,0,4,5,5,6,6,7,7,4,0,4,1,5,2,6,3,7]),i=new St;i.setIndex(new on(n,1)),i.setAttribute("position",new $e([1,1,1,-1,1,1,-1,-1,1,1,-1,1,1,1,-1,-1,1,-1,-1,-1,-1,1,-1,-1],3)),super(i,new Ss({color:t,toneMapped:!1})),this.box=e,this.type="Box3Helper",this.geometry.computeBoundingSphere()}updateMatrixWorld(e){let t=this.box;t.isEmpty()||(t.getCenter(this.position),t.getSize(this.scale),this.scale.multiplyScalar(.5),super.updateMatrixWorld(e))}dispose(){this.geometry.dispose(),this.material.dispose()}};var a_=new N;var qa=class extends bi{constructor(e,t=null){super(),this.object=e,this.domElement=t,this.enabled=!0,this.state=-1,this.keys={},this.mouseButtons={LEFT:null,MIDDLE:null,RIGHT:null},this.touches={ONE:null,TWO:null}}connect(e){e!==void 0?(this.domElement!==null&&this.disconnect(),this.domElement=e):tt("Controls: connect() now requires an element.")}disconnect(){}dispose(){}update(){}};function Bu(r,e,t,n){let i=function(s){switch(s){case Yn:case Zh:return{byteLength:1,components:1};case Bs:case Jh:case wi:return{byteLength:2,components:1};case nc:case ic:return{byteLength:2,components:4};case qi:case tc:case si:return{byteLength:4,components:1};case Kh:case $h:return{byteLength:4,components:3}}throw new Error(`Unknown texture type ${s}.`)}(n);switch(t){case 1021:return r*e;case rc:case sc:return r*e/i.components*i.byteLength;case 1030:case 1031:return r*e*2/i.components*i.byteLength;case 1022:return r*e*3/i.components*i.byteLength;case fi:case 1033:return r*e*4/i.components*i.byteLength;case 33776:case 33777:return Math.floor((r+3)/4)*Math.floor((e+3)/4)*8;case 33778:case 33779:return Math.floor((r+3)/4)*Math.floor((e+3)/4)*16;case 35841:case 35843:return Math.max(r,16)*Math.max(e,8)/4;case 35840:case 35842:return Math.max(r,8)*Math.max(e,8)/2;case 36196:case 37492:case 37488:case 37489:return Math.floor((r+3)/4)*Math.floor((e+3)/4)*8;case 37496:case 37490:case 37491:case 37808:return Math.floor((r+3)/4)*Math.floor((e+3)/4)*16;case 37809:return Math.floor((r+4)/5)*Math.floor((e+3)/4)*16;case 37810:return Math.floor((r+4)/5)*Math.floor((e+4)/5)*16;case 37811:return Math.floor((r+5)/6)*Math.floor((e+4)/5)*16;case 37812:return Math.floor((r+5)/6)*Math.floor((e+5)/6)*16;case 37813:return Math.floor((r+7)/8)*Math.floor((e+4)/5)*16;case 37814:return Math.floor((r+7)/8)*Math.floor((e+5)/6)*16;case 37815:return Math.floor((r+7)/8)*Math.floor((e+7)/8)*16;case 37816:return Math.floor((r+9)/10)*Math.floor((e+4)/5)*16;case 37817:return Math.floor((r+9)/10)*Math.floor((e+5)/6)*16;case 37818:return Math.floor((r+9)/10)*Math.floor((e+7)/8)*16;case 37819:return Math.floor((r+9)/10)*Math.floor((e+9)/10)*16;case 37820:return Math.floor((r+11)/12)*Math.floor((e+9)/10)*16;case 37821:return Math.floor((r+11)/12)*Math.floor((e+11)/12)*16;case 36492:case 36494:case 36495:return Math.ceil(r/4)*Math.ceil(e/4)*16;case 36283:case 36284:return Math.ceil(r/4)*Math.ceil(e/4)*8;case 36285:case 36286:return Math.ceil(r/4)*Math.ceil(e/4)*16}throw new Error(`Unable to determine texture byte length for ${t} format.`)}typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("register",{detail:{revision:nl}})),typeof window<"u"&&(window.__THREE__?tt("WARNING: Multiple instances of Three.js being imported."):window.__THREE__=nl);function Cf(){let r=null,e=!1,t=null,n=null;function i(s,a){t(s,a),n=r.requestAnimationFrame(i)}return{start:function(){e!==!0&&t!==null&&(n=r.requestAnimationFrame(i),e=!0)},stop:function(){r.cancelAnimationFrame(n),e=!1},setAnimationLoop:function(s){t=s},setContext:function(s){r=s}}}function Wm(r){let e=new WeakMap;return{get:function(t){return t.isInterleavedBufferAttribute&&(t=t.data),e.get(t)},remove:function(t){t.isInterleavedBufferAttribute&&(t=t.data);let n=e.get(t);n&&(r.deleteBuffer(n.buffer),e.delete(t))},update:function(t,n){if(t.isInterleavedBufferAttribute&&(t=t.data),t.isGLBufferAttribute){let s=e.get(t);return void((!s||s.version<t.version)&&e.set(t,{buffer:t.buffer,type:t.type,bytesPerElement:t.elementSize,version:t.version}))}let i=e.get(t);if(i===void 0)e.set(t,function(s,a){let o=s.array,c=s.usage,l=o.byteLength,h=r.createBuffer(),p;if(r.bindBuffer(a,h),r.bufferData(a,o,c),s.onUploadCallback(),o instanceof Float32Array)p=r.FLOAT;else if(typeof Float16Array<"u"&&o instanceof Float16Array)p=r.HALF_FLOAT;else if(o instanceof Uint16Array)p=s.isFloat16BufferAttribute?r.HALF_FLOAT:r.UNSIGNED_SHORT;else if(o instanceof Int16Array)p=r.SHORT;else if(o instanceof Uint32Array)p=r.UNSIGNED_INT;else if(o instanceof Int32Array)p=r.INT;else if(o instanceof Int8Array)p=r.BYTE;else if(o instanceof Uint8Array)p=r.UNSIGNED_BYTE;else{if(!(o instanceof Uint8ClampedArray))throw new Error("THREE.WebGLAttributes: Unsupported buffer data format: "+o);p=r.UNSIGNED_BYTE}return{buffer:h,type:p,bytesPerElement:o.BYTES_PER_ELEMENT,version:s.version,size:l}}(t,n));else if(i.version<t.version){if(i.size!==t.array.byteLength)throw new Error("THREE.WebGLAttributes: The size of the buffer attribute's array buffer does not match the original size. Resizing buffer attributes is not supported.");(function(s,a,o){let c=a.array,l=a.updateRanges;if(r.bindBuffer(o,s),l.length===0)r.bufferSubData(o,0,c);else{l.sort((p,f)=>p.start-f.start);let h=0;for(let p=1;p<l.length;p++){let f=l[h],m=l[p];m.start<=f.start+f.count+1?f.count=Math.max(f.count,m.start+m.count-f.start):(++h,l[h]=m)}l.length=h+1;for(let p=0,f=l.length;p<f;p++){let m=l[p];r.bufferSubData(o,m.start*c.BYTES_PER_ELEMENT,c,m.start,m.count)}a.clearUpdateRanges()}a.onUploadCallback()})(i.buffer,t,n),i.version=t.version}}}}var pt={alphahash_fragment:`#ifdef USE_ALPHAHASH
	if ( diffuseColor.a < getAlphaHashThreshold( vPosition ) ) discard;
#endif`,alphahash_pars_fragment:`#ifdef USE_ALPHAHASH
	const float ALPHA_HASH_SCALE = 0.05;
	float hash2D( vec2 value ) {
		return fract( 1.0e4 * sin( 17.0 * value.x + 0.1 * value.y ) * ( 0.1 + abs( sin( 13.0 * value.y + value.x ) ) ) );
	}
	float hash3D( vec3 value ) {
		return hash2D( vec2( hash2D( value.xy ), value.z ) );
	}
	float getAlphaHashThreshold( vec3 position ) {
		float maxDeriv = max(
			length( dFdx( position.xyz ) ),
			length( dFdy( position.xyz ) )
		);
		float pixScale = 1.0 / ( ALPHA_HASH_SCALE * maxDeriv );
		vec2 pixScales = vec2(
			exp2( floor( log2( pixScale ) ) ),
			exp2( ceil( log2( pixScale ) ) )
		);
		vec2 alpha = vec2(
			hash3D( floor( pixScales.x * position.xyz ) ),
			hash3D( floor( pixScales.y * position.xyz ) )
		);
		float lerpFactor = fract( log2( pixScale ) );
		float x = ( 1.0 - lerpFactor ) * alpha.x + lerpFactor * alpha.y;
		float a = min( lerpFactor, 1.0 - lerpFactor );
		vec3 cases = vec3(
			x * x / ( 2.0 * a * ( 1.0 - a ) ),
			( x - 0.5 * a ) / ( 1.0 - a ),
			1.0 - ( ( 1.0 - x ) * ( 1.0 - x ) / ( 2.0 * a * ( 1.0 - a ) ) )
		);
		float threshold = ( x < ( 1.0 - a ) )
			? ( ( x < a ) ? cases.x : cases.y )
			: cases.z;
		return clamp( threshold , 1.0e-6, 1.0 );
	}
#endif`,alphamap_fragment:`#ifdef USE_ALPHAMAP
	diffuseColor.a *= texture2D( alphaMap, vAlphaMapUv ).g;
#endif`,alphamap_pars_fragment:`#ifdef USE_ALPHAMAP
	uniform sampler2D alphaMap;
#endif`,alphatest_fragment:`#ifdef USE_ALPHATEST
	#ifdef ALPHA_TO_COVERAGE
	diffuseColor.a = smoothstep( alphaTest, alphaTest + fwidth( diffuseColor.a ), diffuseColor.a );
	if ( diffuseColor.a == 0.0 ) discard;
	#else
	if ( diffuseColor.a < alphaTest ) discard;
	#endif
#endif`,alphatest_pars_fragment:`#ifdef USE_ALPHATEST
	uniform float alphaTest;
#endif`,aomap_fragment:`#ifdef USE_AOMAP
	float ambientOcclusion = ( texture2D( aoMap, vAoMapUv ).r - 1.0 ) * aoMapIntensity + 1.0;
	reflectedLight.indirectDiffuse *= ambientOcclusion;
	#if defined( USE_CLEARCOAT ) 
		clearcoatSpecularIndirect *= ambientOcclusion;
	#endif
	#if defined( USE_SHEEN ) 
		sheenSpecularIndirect *= ambientOcclusion;
	#endif
	#if defined( USE_ENVMAP ) && defined( STANDARD )
		float dotNV = saturate( dot( geometryNormal, geometryViewDir ) );
		reflectedLight.indirectSpecular *= computeSpecularOcclusion( dotNV, ambientOcclusion, material.roughness );
	#endif
#endif`,aomap_pars_fragment:`#ifdef USE_AOMAP
	uniform sampler2D aoMap;
	uniform float aoMapIntensity;
#endif`,batching_pars_vertex:`#ifdef USE_BATCHING
	#if ! defined( GL_ANGLE_multi_draw )
	#define gl_DrawID _gl_DrawID
	uniform int _gl_DrawID;
	#endif
	uniform highp sampler2D batchingTexture;
	uniform highp usampler2D batchingIdTexture;
	mat4 getBatchingMatrix( const in float i ) {
		int size = textureSize( batchingTexture, 0 ).x;
		int j = int( i ) * 4;
		int x = j % size;
		int y = j / size;
		vec4 v1 = texelFetch( batchingTexture, ivec2( x, y ), 0 );
		vec4 v2 = texelFetch( batchingTexture, ivec2( x + 1, y ), 0 );
		vec4 v3 = texelFetch( batchingTexture, ivec2( x + 2, y ), 0 );
		vec4 v4 = texelFetch( batchingTexture, ivec2( x + 3, y ), 0 );
		return mat4( v1, v2, v3, v4 );
	}
	float getIndirectIndex( const in int i ) {
		int size = textureSize( batchingIdTexture, 0 ).x;
		int x = i % size;
		int y = i / size;
		return float( texelFetch( batchingIdTexture, ivec2( x, y ), 0 ).r );
	}
#endif
#ifdef USE_BATCHING_COLOR
	uniform sampler2D batchingColorTexture;
	vec4 getBatchingColor( const in float i ) {
		int size = textureSize( batchingColorTexture, 0 ).x;
		int j = int( i );
		int x = j % size;
		int y = j / size;
		return texelFetch( batchingColorTexture, ivec2( x, y ), 0 );
	}
#endif`,batching_vertex:`#ifdef USE_BATCHING
	mat4 batchingMatrix = getBatchingMatrix( getIndirectIndex( gl_DrawID ) );
#endif`,begin_vertex:`vec3 transformed = vec3( position );
#ifdef USE_ALPHAHASH
	vPosition = vec3( position );
#endif`,beginnormal_vertex:`vec3 objectNormal = vec3( normal );
#ifdef USE_TANGENT
	vec3 objectTangent = vec3( tangent.xyz );
#endif`,bsdfs:`float G_BlinnPhong_Implicit( ) {
	return 0.25;
}
float D_BlinnPhong( const in float shininess, const in float dotNH ) {
	return RECIPROCAL_PI * ( shininess * 0.5 + 1.0 ) * pow( dotNH, shininess );
}
vec3 BRDF_BlinnPhong( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in vec3 specularColor, const in float shininess ) {
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNH = saturate( dot( normal, halfDir ) );
	float dotVH = saturate( dot( viewDir, halfDir ) );
	vec3 F = F_Schlick( specularColor, 1.0, dotVH );
	float G = G_BlinnPhong_Implicit( );
	float D = D_BlinnPhong( shininess, dotNH );
	return F * ( G * D );
} // validated`,iridescence_fragment:`#ifdef USE_IRIDESCENCE
	const mat3 XYZ_TO_REC709 = mat3(
		 3.2404542, -0.9692660,  0.0556434,
		-1.5371385,  1.8760108, -0.2040259,
		-0.4985314,  0.0415560,  1.0572252
	);
	vec3 Fresnel0ToIor( vec3 fresnel0 ) {
		vec3 sqrtF0 = sqrt( fresnel0 );
		return ( vec3( 1.0 ) + sqrtF0 ) / ( vec3( 1.0 ) - sqrtF0 );
	}
	vec3 IorToFresnel0( vec3 transmittedIor, float incidentIor ) {
		return pow2( ( transmittedIor - vec3( incidentIor ) ) / ( transmittedIor + vec3( incidentIor ) ) );
	}
	float IorToFresnel0( float transmittedIor, float incidentIor ) {
		return pow2( ( transmittedIor - incidentIor ) / ( transmittedIor + incidentIor ));
	}
	vec3 evalSensitivity( float OPD, vec3 shift ) {
		float phase = 2.0 * PI * OPD * 1.0e-9;
		vec3 val = vec3( 5.4856e-13, 4.4201e-13, 5.2481e-13 );
		vec3 pos = vec3( 1.6810e+06, 1.7953e+06, 2.2084e+06 );
		vec3 var = vec3( 4.3278e+09, 9.3046e+09, 6.6121e+09 );
		vec3 xyz = val * sqrt( 2.0 * PI * var ) * cos( pos * phase + shift ) * exp( - pow2( phase ) * var );
		xyz.x += 9.7470e-14 * sqrt( 2.0 * PI * 4.5282e+09 ) * cos( 2.2399e+06 * phase + shift[ 0 ] ) * exp( - 4.5282e+09 * pow2( phase ) );
		xyz /= 1.0685e-7;
		vec3 rgb = XYZ_TO_REC709 * xyz;
		return rgb;
	}
	vec3 evalIridescence( float outsideIOR, float eta2, float cosTheta1, float thinFilmThickness, vec3 baseF0 ) {
		vec3 I;
		float iridescenceIOR = mix( outsideIOR, eta2, smoothstep( 0.0, 0.03, thinFilmThickness ) );
		float sinTheta2Sq = pow2( outsideIOR / iridescenceIOR ) * ( 1.0 - pow2( cosTheta1 ) );
		float cosTheta2Sq = 1.0 - sinTheta2Sq;
		if ( cosTheta2Sq < 0.0 ) {
			return vec3( 1.0 );
		}
		float cosTheta2 = sqrt( cosTheta2Sq );
		float R0 = IorToFresnel0( iridescenceIOR, outsideIOR );
		float R12 = F_Schlick( R0, 1.0, cosTheta1 );
		float T121 = 1.0 - R12;
		float phi12 = 0.0;
		if ( iridescenceIOR < outsideIOR ) phi12 = PI;
		float phi21 = PI - phi12;
		vec3 baseIOR = Fresnel0ToIor( clamp( baseF0, 0.0, 0.9999 ) );		vec3 R1 = IorToFresnel0( baseIOR, iridescenceIOR );
		vec3 R23 = F_Schlick( R1, 1.0, cosTheta2 );
		vec3 phi23 = vec3( 0.0 );
		if ( baseIOR[ 0 ] < iridescenceIOR ) phi23[ 0 ] = PI;
		if ( baseIOR[ 1 ] < iridescenceIOR ) phi23[ 1 ] = PI;
		if ( baseIOR[ 2 ] < iridescenceIOR ) phi23[ 2 ] = PI;
		float OPD = 2.0 * iridescenceIOR * thinFilmThickness * cosTheta2;
		vec3 phi = vec3( phi21 ) + phi23;
		vec3 R123 = clamp( R12 * R23, 1e-5, 0.9999 );
		vec3 r123 = sqrt( R123 );
		vec3 Rs = pow2( T121 ) * R23 / ( vec3( 1.0 ) - R123 );
		vec3 C0 = R12 + Rs;
		I = C0;
		vec3 Cm = Rs - T121;
		for ( int m = 1; m <= 2; ++ m ) {
			Cm *= r123;
			vec3 Sm = 2.0 * evalSensitivity( float( m ) * OPD, float( m ) * phi );
			I += Cm * Sm;
		}
		return max( I, vec3( 0.0 ) );
	}
#endif`,bumpmap_pars_fragment:`#ifdef USE_BUMPMAP
	uniform sampler2D bumpMap;
	uniform float bumpScale;
	vec2 dHdxy_fwd() {
		vec2 dSTdx = dFdx( vBumpMapUv );
		vec2 dSTdy = dFdy( vBumpMapUv );
		float Hll = bumpScale * texture2D( bumpMap, vBumpMapUv ).x;
		float dBx = bumpScale * texture2D( bumpMap, vBumpMapUv + dSTdx ).x - Hll;
		float dBy = bumpScale * texture2D( bumpMap, vBumpMapUv + dSTdy ).x - Hll;
		return vec2( dBx, dBy );
	}
	vec3 perturbNormalArb( vec3 surf_pos, vec3 surf_norm, vec2 dHdxy, float faceDirection ) {
		vec3 vSigmaX = normalize( dFdx( surf_pos.xyz ) );
		vec3 vSigmaY = normalize( dFdy( surf_pos.xyz ) );
		vec3 vN = surf_norm;
		vec3 R1 = cross( vSigmaY, vN );
		vec3 R2 = cross( vN, vSigmaX );
		float fDet = dot( vSigmaX, R1 ) * faceDirection;
		vec3 vGrad = sign( fDet ) * ( dHdxy.x * R1 + dHdxy.y * R2 );
		return normalize( abs( fDet ) * surf_norm - vGrad );
	}
#endif`,clipping_planes_fragment:`#if NUM_CLIPPING_PLANES > 0
	vec4 plane;
	#ifdef ALPHA_TO_COVERAGE
		float distanceToPlane, distanceGradient;
		float clipOpacity = 1.0;
		#pragma unroll_loop_start
		for ( int i = 0; i < UNION_CLIPPING_PLANES; i ++ ) {
			plane = clippingPlanes[ i ];
			distanceToPlane = - dot( vClipPosition, plane.xyz ) + plane.w;
			distanceGradient = fwidth( distanceToPlane ) / 2.0;
			clipOpacity *= smoothstep( - distanceGradient, distanceGradient, distanceToPlane );
			if ( clipOpacity == 0.0 ) discard;
		}
		#pragma unroll_loop_end
		#if UNION_CLIPPING_PLANES < NUM_CLIPPING_PLANES
			float unionClipOpacity = 1.0;
			#pragma unroll_loop_start
			for ( int i = UNION_CLIPPING_PLANES; i < NUM_CLIPPING_PLANES; i ++ ) {
				plane = clippingPlanes[ i ];
				distanceToPlane = - dot( vClipPosition, plane.xyz ) + plane.w;
				distanceGradient = fwidth( distanceToPlane ) / 2.0;
				unionClipOpacity *= 1.0 - smoothstep( - distanceGradient, distanceGradient, distanceToPlane );
			}
			#pragma unroll_loop_end
			clipOpacity *= 1.0 - unionClipOpacity;
		#endif
		diffuseColor.a *= clipOpacity;
		if ( diffuseColor.a == 0.0 ) discard;
	#else
		#pragma unroll_loop_start
		for ( int i = 0; i < UNION_CLIPPING_PLANES; i ++ ) {
			plane = clippingPlanes[ i ];
			if ( dot( vClipPosition, plane.xyz ) > plane.w ) discard;
		}
		#pragma unroll_loop_end
		#if UNION_CLIPPING_PLANES < NUM_CLIPPING_PLANES
			bool clipped = true;
			#pragma unroll_loop_start
			for ( int i = UNION_CLIPPING_PLANES; i < NUM_CLIPPING_PLANES; i ++ ) {
				plane = clippingPlanes[ i ];
				clipped = ( dot( vClipPosition, plane.xyz ) > plane.w ) && clipped;
			}
			#pragma unroll_loop_end
			if ( clipped ) discard;
		#endif
	#endif
#endif`,clipping_planes_pars_fragment:`#if NUM_CLIPPING_PLANES > 0
	varying vec3 vClipPosition;
	uniform vec4 clippingPlanes[ NUM_CLIPPING_PLANES ];
#endif`,clipping_planes_pars_vertex:`#if NUM_CLIPPING_PLANES > 0
	varying vec3 vClipPosition;
#endif`,clipping_planes_vertex:`#if NUM_CLIPPING_PLANES > 0
	vClipPosition = - mvPosition.xyz;
#endif`,color_fragment:`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )
	diffuseColor *= vColor;
#endif`,color_pars_fragment:`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA )
	varying vec4 vColor;
#endif`,color_pars_vertex:`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA ) || defined( USE_INSTANCING_COLOR ) || defined( USE_BATCHING_COLOR )
	varying vec4 vColor;
#endif`,color_vertex:`#if defined( USE_COLOR ) || defined( USE_COLOR_ALPHA ) || defined( USE_INSTANCING_COLOR ) || defined( USE_BATCHING_COLOR )
	vColor = vec4( 1.0 );
#endif
#ifdef USE_COLOR_ALPHA
	vColor *= color;
#elif defined( USE_COLOR )
	vColor.rgb *= color;
#endif
#ifdef USE_INSTANCING_COLOR
	vColor.rgb *= instanceColor.rgb;
#endif
#ifdef USE_BATCHING_COLOR
	vColor *= getBatchingColor( getIndirectIndex( gl_DrawID ) );
#endif`,common:`#define PI 3.141592653589793
#define PI2 6.283185307179586
#define PI_HALF 1.5707963267948966
#define RECIPROCAL_PI 0.3183098861837907
#define RECIPROCAL_PI2 0.15915494309189535
#define EPSILON 1e-6
#ifndef saturate
#define saturate( a ) clamp( a, 0.0, 1.0 )
#endif
#define whiteComplement( a ) ( 1.0 - saturate( a ) )
float pow2( const in float x ) { return x*x; }
vec3 pow2( const in vec3 x ) { return x*x; }
float pow3( const in float x ) { return x*x*x; }
float pow4( const in float x ) { float x2 = x*x; return x2*x2; }
float max3( const in vec3 v ) { return max( max( v.x, v.y ), v.z ); }
float average( const in vec3 v ) { return dot( v, vec3( 0.3333333 ) ); }
highp float rand( const in vec2 uv ) {
	const highp float a = 12.9898, b = 78.233, c = 43758.5453;
	highp float dt = dot( uv.xy, vec2( a,b ) ), sn = mod( dt, PI );
	return fract( sin( sn ) * c );
}
#ifdef HIGH_PRECISION
	float precisionSafeLength( vec3 v ) { return length( v ); }
#else
	float precisionSafeLength( vec3 v ) {
		float maxComponent = max3( abs( v ) );
		return length( v / maxComponent ) * maxComponent;
	}
#endif
struct IncidentLight {
	vec3 color;
	vec3 direction;
	bool visible;
};
struct ReflectedLight {
	vec3 directDiffuse;
	vec3 directSpecular;
	vec3 indirectDiffuse;
	vec3 indirectSpecular;
};
#ifdef USE_ALPHAHASH
	varying vec3 vPosition;
#endif
vec3 transformDirection( in vec3 dir, in mat4 matrix ) {
	return normalize( ( matrix * vec4( dir, 0.0 ) ).xyz );
}
vec3 inverseTransformDirection( in vec3 dir, in mat4 matrix ) {
	return normalize( ( vec4( dir, 0.0 ) * matrix ).xyz );
}
bool isPerspectiveMatrix( mat4 m ) {
	return m[ 2 ][ 3 ] == - 1.0;
}
vec2 equirectUv( in vec3 dir ) {
	float u = atan( dir.z, dir.x ) * RECIPROCAL_PI2 + 0.5;
	float v = asin( clamp( dir.y, - 1.0, 1.0 ) ) * RECIPROCAL_PI + 0.5;
	return vec2( u, v );
}
vec3 BRDF_Lambert( const in vec3 diffuseColor ) {
	return RECIPROCAL_PI * diffuseColor;
}
vec3 F_Schlick( const in vec3 f0, const in float f90, const in float dotVH ) {
	float fresnel = exp2( ( - 5.55473 * dotVH - 6.98316 ) * dotVH );
	return f0 * ( 1.0 - fresnel ) + ( f90 * fresnel );
}
float F_Schlick( const in float f0, const in float f90, const in float dotVH ) {
	float fresnel = exp2( ( - 5.55473 * dotVH - 6.98316 ) * dotVH );
	return f0 * ( 1.0 - fresnel ) + ( f90 * fresnel );
} // validated`,cube_uv_reflection_fragment:`#ifdef ENVMAP_TYPE_CUBE_UV
	#define cubeUV_minMipLevel 4.0
	#define cubeUV_minTileSize 16.0
	float getFace( vec3 direction ) {
		vec3 absDirection = abs( direction );
		float face = - 1.0;
		if ( absDirection.x > absDirection.z ) {
			if ( absDirection.x > absDirection.y )
				face = direction.x > 0.0 ? 0.0 : 3.0;
			else
				face = direction.y > 0.0 ? 1.0 : 4.0;
		} else {
			if ( absDirection.z > absDirection.y )
				face = direction.z > 0.0 ? 2.0 : 5.0;
			else
				face = direction.y > 0.0 ? 1.0 : 4.0;
		}
		return face;
	}
	vec2 getUV( vec3 direction, float face ) {
		vec2 uv;
		if ( face == 0.0 ) {
			uv = vec2( direction.z, direction.y ) / abs( direction.x );
		} else if ( face == 1.0 ) {
			uv = vec2( - direction.x, - direction.z ) / abs( direction.y );
		} else if ( face == 2.0 ) {
			uv = vec2( - direction.x, direction.y ) / abs( direction.z );
		} else if ( face == 3.0 ) {
			uv = vec2( - direction.z, direction.y ) / abs( direction.x );
		} else if ( face == 4.0 ) {
			uv = vec2( - direction.x, direction.z ) / abs( direction.y );
		} else {
			uv = vec2( direction.x, direction.y ) / abs( direction.z );
		}
		return 0.5 * ( uv + 1.0 );
	}
	vec3 bilinearCubeUV( sampler2D envMap, vec3 direction, float mipInt ) {
		float face = getFace( direction );
		float filterInt = max( cubeUV_minMipLevel - mipInt, 0.0 );
		mipInt = max( mipInt, cubeUV_minMipLevel );
		float faceSize = exp2( mipInt );
		highp vec2 uv = getUV( direction, face ) * ( faceSize - 2.0 ) + 1.0;
		if ( face > 2.0 ) {
			uv.y += faceSize;
			face -= 3.0;
		}
		uv.x += face * faceSize;
		uv.x += filterInt * 3.0 * cubeUV_minTileSize;
		uv.y += 4.0 * ( exp2( CUBEUV_MAX_MIP ) - faceSize );
		uv.x *= CUBEUV_TEXEL_WIDTH;
		uv.y *= CUBEUV_TEXEL_HEIGHT;
		#ifdef texture2DGradEXT
			return texture2DGradEXT( envMap, uv, vec2( 0.0 ), vec2( 0.0 ) ).rgb;
		#else
			return texture2D( envMap, uv ).rgb;
		#endif
	}
	#define cubeUV_r0 1.0
	#define cubeUV_m0 - 2.0
	#define cubeUV_r1 0.8
	#define cubeUV_m1 - 1.0
	#define cubeUV_r4 0.4
	#define cubeUV_m4 2.0
	#define cubeUV_r5 0.305
	#define cubeUV_m5 3.0
	#define cubeUV_r6 0.21
	#define cubeUV_m6 4.0
	float roughnessToMip( float roughness ) {
		float mip = 0.0;
		if ( roughness >= cubeUV_r1 ) {
			mip = ( cubeUV_r0 - roughness ) * ( cubeUV_m1 - cubeUV_m0 ) / ( cubeUV_r0 - cubeUV_r1 ) + cubeUV_m0;
		} else if ( roughness >= cubeUV_r4 ) {
			mip = ( cubeUV_r1 - roughness ) * ( cubeUV_m4 - cubeUV_m1 ) / ( cubeUV_r1 - cubeUV_r4 ) + cubeUV_m1;
		} else if ( roughness >= cubeUV_r5 ) {
			mip = ( cubeUV_r4 - roughness ) * ( cubeUV_m5 - cubeUV_m4 ) / ( cubeUV_r4 - cubeUV_r5 ) + cubeUV_m4;
		} else if ( roughness >= cubeUV_r6 ) {
			mip = ( cubeUV_r5 - roughness ) * ( cubeUV_m6 - cubeUV_m5 ) / ( cubeUV_r5 - cubeUV_r6 ) + cubeUV_m5;
		} else {
			mip = - 2.0 * log2( 1.16 * roughness );		}
		return mip;
	}
	vec4 textureCubeUV( sampler2D envMap, vec3 sampleDir, float roughness ) {
		float mip = clamp( roughnessToMip( roughness ), cubeUV_m0, CUBEUV_MAX_MIP );
		float mipF = fract( mip );
		float mipInt = floor( mip );
		vec3 color0 = bilinearCubeUV( envMap, sampleDir, mipInt );
		if ( mipF == 0.0 ) {
			return vec4( color0, 1.0 );
		} else {
			vec3 color1 = bilinearCubeUV( envMap, sampleDir, mipInt + 1.0 );
			return vec4( mix( color0, color1, mipF ), 1.0 );
		}
	}
#endif`,defaultnormal_vertex:`vec3 transformedNormal = objectNormal;
#ifdef USE_TANGENT
	vec3 transformedTangent = objectTangent;
#endif
#ifdef USE_BATCHING
	mat3 bm = mat3( batchingMatrix );
	transformedNormal /= vec3( dot( bm[ 0 ], bm[ 0 ] ), dot( bm[ 1 ], bm[ 1 ] ), dot( bm[ 2 ], bm[ 2 ] ) );
	transformedNormal = bm * transformedNormal;
	#ifdef USE_TANGENT
		transformedTangent = bm * transformedTangent;
	#endif
#endif
#ifdef USE_INSTANCING
	mat3 im = mat3( instanceMatrix );
	transformedNormal /= vec3( dot( im[ 0 ], im[ 0 ] ), dot( im[ 1 ], im[ 1 ] ), dot( im[ 2 ], im[ 2 ] ) );
	transformedNormal = im * transformedNormal;
	#ifdef USE_TANGENT
		transformedTangent = im * transformedTangent;
	#endif
#endif
transformedNormal = normalMatrix * transformedNormal;
#ifdef FLIP_SIDED
	transformedNormal = - transformedNormal;
#endif
#ifdef USE_TANGENT
	transformedTangent = ( modelViewMatrix * vec4( transformedTangent, 0.0 ) ).xyz;
	#ifdef FLIP_SIDED
		transformedTangent = - transformedTangent;
	#endif
#endif`,displacementmap_pars_vertex:`#ifdef USE_DISPLACEMENTMAP
	uniform sampler2D displacementMap;
	uniform float displacementScale;
	uniform float displacementBias;
#endif`,displacementmap_vertex:`#ifdef USE_DISPLACEMENTMAP
	transformed += normalize( objectNormal ) * ( texture2D( displacementMap, vDisplacementMapUv ).x * displacementScale + displacementBias );
#endif`,emissivemap_fragment:`#ifdef USE_EMISSIVEMAP
	vec4 emissiveColor = texture2D( emissiveMap, vEmissiveMapUv );
	#ifdef DECODE_VIDEO_TEXTURE_EMISSIVE
		emissiveColor = sRGBTransferEOTF( emissiveColor );
	#endif
	totalEmissiveRadiance *= emissiveColor.rgb;
#endif`,emissivemap_pars_fragment:`#ifdef USE_EMISSIVEMAP
	uniform sampler2D emissiveMap;
#endif`,colorspace_fragment:"gl_FragColor = linearToOutputTexel( gl_FragColor );",colorspace_pars_fragment:`vec4 LinearTransferOETF( in vec4 value ) {
	return value;
}
vec4 sRGBTransferEOTF( in vec4 value ) {
	return vec4( mix( pow( value.rgb * 0.9478672986 + vec3( 0.0521327014 ), vec3( 2.4 ) ), value.rgb * 0.0773993808, vec3( lessThanEqual( value.rgb, vec3( 0.04045 ) ) ) ), value.a );
}
vec4 sRGBTransferOETF( in vec4 value ) {
	return vec4( mix( pow( value.rgb, vec3( 0.41666 ) ) * 1.055 - vec3( 0.055 ), value.rgb * 12.92, vec3( lessThanEqual( value.rgb, vec3( 0.0031308 ) ) ) ), value.a );
}`,envmap_fragment:`#ifdef USE_ENVMAP
	#ifdef ENV_WORLDPOS
		vec3 cameraToFrag;
		if ( isOrthographic ) {
			cameraToFrag = normalize( vec3( - viewMatrix[ 0 ][ 2 ], - viewMatrix[ 1 ][ 2 ], - viewMatrix[ 2 ][ 2 ] ) );
		} else {
			cameraToFrag = normalize( vWorldPosition - cameraPosition );
		}
		vec3 worldNormal = inverseTransformDirection( normal, viewMatrix );
		#ifdef ENVMAP_MODE_REFLECTION
			vec3 reflectVec = reflect( cameraToFrag, worldNormal );
		#else
			vec3 reflectVec = refract( cameraToFrag, worldNormal, refractionRatio );
		#endif
	#else
		vec3 reflectVec = vReflect;
	#endif
	#ifdef ENVMAP_TYPE_CUBE
		vec4 envColor = textureCube( envMap, envMapRotation * vec3( flipEnvMap * reflectVec.x, reflectVec.yz ) );
		#ifdef ENVMAP_BLENDING_MULTIPLY
			outgoingLight = mix( outgoingLight, outgoingLight * envColor.xyz, specularStrength * reflectivity );
		#elif defined( ENVMAP_BLENDING_MIX )
			outgoingLight = mix( outgoingLight, envColor.xyz, specularStrength * reflectivity );
		#elif defined( ENVMAP_BLENDING_ADD )
			outgoingLight += envColor.xyz * specularStrength * reflectivity;
		#endif
	#endif
#endif`,envmap_common_pars_fragment:`#ifdef USE_ENVMAP
	uniform float envMapIntensity;
	uniform float flipEnvMap;
	uniform mat3 envMapRotation;
	#ifdef ENVMAP_TYPE_CUBE
		uniform samplerCube envMap;
	#else
		uniform sampler2D envMap;
	#endif
#endif`,envmap_pars_fragment:`#ifdef USE_ENVMAP
	uniform float reflectivity;
	#if defined( USE_BUMPMAP ) || defined( USE_NORMALMAP ) || defined( PHONG ) || defined( LAMBERT )
		#define ENV_WORLDPOS
	#endif
	#ifdef ENV_WORLDPOS
		varying vec3 vWorldPosition;
		uniform float refractionRatio;
	#else
		varying vec3 vReflect;
	#endif
#endif`,envmap_pars_vertex:`#ifdef USE_ENVMAP
	#if defined( USE_BUMPMAP ) || defined( USE_NORMALMAP ) || defined( PHONG ) || defined( LAMBERT )
		#define ENV_WORLDPOS
	#endif
	#ifdef ENV_WORLDPOS
		
		varying vec3 vWorldPosition;
	#else
		varying vec3 vReflect;
		uniform float refractionRatio;
	#endif
#endif`,envmap_physical_pars_fragment:`#ifdef USE_ENVMAP
	vec3 getIBLIrradiance( const in vec3 normal ) {
		#ifdef ENVMAP_TYPE_CUBE_UV
			vec3 worldNormal = inverseTransformDirection( normal, viewMatrix );
			vec4 envMapColor = textureCubeUV( envMap, envMapRotation * worldNormal, 1.0 );
			return PI * envMapColor.rgb * envMapIntensity;
		#else
			return vec3( 0.0 );
		#endif
	}
	vec3 getIBLRadiance( const in vec3 viewDir, const in vec3 normal, const in float roughness ) {
		#ifdef ENVMAP_TYPE_CUBE_UV
			vec3 reflectVec = reflect( - viewDir, normal );
			reflectVec = normalize( mix( reflectVec, normal, pow4( roughness ) ) );
			reflectVec = inverseTransformDirection( reflectVec, viewMatrix );
			vec4 envMapColor = textureCubeUV( envMap, envMapRotation * reflectVec, roughness );
			return envMapColor.rgb * envMapIntensity;
		#else
			return vec3( 0.0 );
		#endif
	}
	#ifdef USE_ANISOTROPY
		vec3 getIBLAnisotropyRadiance( const in vec3 viewDir, const in vec3 normal, const in float roughness, const in vec3 bitangent, const in float anisotropy ) {
			#ifdef ENVMAP_TYPE_CUBE_UV
				vec3 bentNormal = cross( bitangent, viewDir );
				bentNormal = normalize( cross( bentNormal, bitangent ) );
				bentNormal = normalize( mix( bentNormal, normal, pow2( pow2( 1.0 - anisotropy * ( 1.0 - roughness ) ) ) ) );
				return getIBLRadiance( viewDir, bentNormal, roughness );
			#else
				return vec3( 0.0 );
			#endif
		}
	#endif
#endif`,envmap_vertex:`#ifdef USE_ENVMAP
	#ifdef ENV_WORLDPOS
		vWorldPosition = worldPosition.xyz;
	#else
		vec3 cameraToVertex;
		if ( isOrthographic ) {
			cameraToVertex = normalize( vec3( - viewMatrix[ 0 ][ 2 ], - viewMatrix[ 1 ][ 2 ], - viewMatrix[ 2 ][ 2 ] ) );
		} else {
			cameraToVertex = normalize( worldPosition.xyz - cameraPosition );
		}
		vec3 worldNormal = inverseTransformDirection( transformedNormal, viewMatrix );
		#ifdef ENVMAP_MODE_REFLECTION
			vReflect = reflect( cameraToVertex, worldNormal );
		#else
			vReflect = refract( cameraToVertex, worldNormal, refractionRatio );
		#endif
	#endif
#endif`,fog_vertex:`#ifdef USE_FOG
	vFogDepth = - mvPosition.z;
#endif`,fog_pars_vertex:`#ifdef USE_FOG
	varying float vFogDepth;
#endif`,fog_fragment:`#ifdef USE_FOG
	#ifdef FOG_EXP2
		float fogFactor = 1.0 - exp( - fogDensity * fogDensity * vFogDepth * vFogDepth );
	#else
		float fogFactor = smoothstep( fogNear, fogFar, vFogDepth );
	#endif
	gl_FragColor.rgb = mix( gl_FragColor.rgb, fogColor, fogFactor );
#endif`,fog_pars_fragment:`#ifdef USE_FOG
	uniform vec3 fogColor;
	varying float vFogDepth;
	#ifdef FOG_EXP2
		uniform float fogDensity;
	#else
		uniform float fogNear;
		uniform float fogFar;
	#endif
#endif`,gradientmap_pars_fragment:`#ifdef USE_GRADIENTMAP
	uniform sampler2D gradientMap;
#endif
vec3 getGradientIrradiance( vec3 normal, vec3 lightDirection ) {
	float dotNL = dot( normal, lightDirection );
	vec2 coord = vec2( dotNL * 0.5 + 0.5, 0.0 );
	#ifdef USE_GRADIENTMAP
		return vec3( texture2D( gradientMap, coord ).r );
	#else
		vec2 fw = fwidth( coord ) * 0.5;
		return mix( vec3( 0.7 ), vec3( 1.0 ), smoothstep( 0.7 - fw.x, 0.7 + fw.x, coord.x ) );
	#endif
}`,lightmap_pars_fragment:`#ifdef USE_LIGHTMAP
	uniform sampler2D lightMap;
	uniform float lightMapIntensity;
#endif`,lights_lambert_fragment:`LambertMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.specularStrength = specularStrength;`,lights_lambert_pars_fragment:`varying vec3 vViewPosition;
struct LambertMaterial {
	vec3 diffuseColor;
	float specularStrength;
};
void RE_Direct_Lambert( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
void RE_IndirectDiffuse_Lambert( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in LambertMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_Lambert
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Lambert`,lights_pars_begin:`uniform bool receiveShadow;
uniform vec3 ambientLightColor;
#if defined( USE_LIGHT_PROBES )
	uniform vec3 lightProbe[ 9 ];
#endif
vec3 shGetIrradianceAt( in vec3 normal, in vec3 shCoefficients[ 9 ] ) {
	float x = normal.x, y = normal.y, z = normal.z;
	vec3 result = shCoefficients[ 0 ] * 0.886227;
	result += shCoefficients[ 1 ] * 2.0 * 0.511664 * y;
	result += shCoefficients[ 2 ] * 2.0 * 0.511664 * z;
	result += shCoefficients[ 3 ] * 2.0 * 0.511664 * x;
	result += shCoefficients[ 4 ] * 2.0 * 0.429043 * x * y;
	result += shCoefficients[ 5 ] * 2.0 * 0.429043 * y * z;
	result += shCoefficients[ 6 ] * ( 0.743125 * z * z - 0.247708 );
	result += shCoefficients[ 7 ] * 2.0 * 0.429043 * x * z;
	result += shCoefficients[ 8 ] * 0.429043 * ( x * x - y * y );
	return result;
}
vec3 getLightProbeIrradiance( const in vec3 lightProbe[ 9 ], const in vec3 normal ) {
	vec3 worldNormal = inverseTransformDirection( normal, viewMatrix );
	vec3 irradiance = shGetIrradianceAt( worldNormal, lightProbe );
	return irradiance;
}
vec3 getAmbientLightIrradiance( const in vec3 ambientLightColor ) {
	vec3 irradiance = ambientLightColor;
	return irradiance;
}
float getDistanceAttenuation( const in float lightDistance, const in float cutoffDistance, const in float decayExponent ) {
	float distanceFalloff = 1.0 / max( pow( lightDistance, decayExponent ), 0.01 );
	if ( cutoffDistance > 0.0 ) {
		distanceFalloff *= pow2( saturate( 1.0 - pow4( lightDistance / cutoffDistance ) ) );
	}
	return distanceFalloff;
}
float getSpotAttenuation( const in float coneCosine, const in float penumbraCosine, const in float angleCosine ) {
	return smoothstep( coneCosine, penumbraCosine, angleCosine );
}
#if NUM_DIR_LIGHTS > 0
	struct DirectionalLight {
		vec3 direction;
		vec3 color;
	};
	uniform DirectionalLight directionalLights[ NUM_DIR_LIGHTS ];
	void getDirectionalLightInfo( const in DirectionalLight directionalLight, out IncidentLight light ) {
		light.color = directionalLight.color;
		light.direction = directionalLight.direction;
		light.visible = true;
	}
#endif
#if NUM_POINT_LIGHTS > 0
	struct PointLight {
		vec3 position;
		vec3 color;
		float distance;
		float decay;
	};
	uniform PointLight pointLights[ NUM_POINT_LIGHTS ];
	void getPointLightInfo( const in PointLight pointLight, const in vec3 geometryPosition, out IncidentLight light ) {
		vec3 lVector = pointLight.position - geometryPosition;
		light.direction = normalize( lVector );
		float lightDistance = length( lVector );
		light.color = pointLight.color;
		light.color *= getDistanceAttenuation( lightDistance, pointLight.distance, pointLight.decay );
		light.visible = ( light.color != vec3( 0.0 ) );
	}
#endif
#if NUM_SPOT_LIGHTS > 0
	struct SpotLight {
		vec3 position;
		vec3 direction;
		vec3 color;
		float distance;
		float decay;
		float coneCos;
		float penumbraCos;
	};
	uniform SpotLight spotLights[ NUM_SPOT_LIGHTS ];
	void getSpotLightInfo( const in SpotLight spotLight, const in vec3 geometryPosition, out IncidentLight light ) {
		vec3 lVector = spotLight.position - geometryPosition;
		light.direction = normalize( lVector );
		float angleCos = dot( light.direction, spotLight.direction );
		float spotAttenuation = getSpotAttenuation( spotLight.coneCos, spotLight.penumbraCos, angleCos );
		if ( spotAttenuation > 0.0 ) {
			float lightDistance = length( lVector );
			light.color = spotLight.color * spotAttenuation;
			light.color *= getDistanceAttenuation( lightDistance, spotLight.distance, spotLight.decay );
			light.visible = ( light.color != vec3( 0.0 ) );
		} else {
			light.color = vec3( 0.0 );
			light.visible = false;
		}
	}
#endif
#if NUM_RECT_AREA_LIGHTS > 0
	struct RectAreaLight {
		vec3 color;
		vec3 position;
		vec3 halfWidth;
		vec3 halfHeight;
	};
	uniform sampler2D ltc_1;	uniform sampler2D ltc_2;
	uniform RectAreaLight rectAreaLights[ NUM_RECT_AREA_LIGHTS ];
#endif
#if NUM_HEMI_LIGHTS > 0
	struct HemisphereLight {
		vec3 direction;
		vec3 skyColor;
		vec3 groundColor;
	};
	uniform HemisphereLight hemisphereLights[ NUM_HEMI_LIGHTS ];
	vec3 getHemisphereLightIrradiance( const in HemisphereLight hemiLight, const in vec3 normal ) {
		float dotNL = dot( normal, hemiLight.direction );
		float hemiDiffuseWeight = 0.5 * dotNL + 0.5;
		vec3 irradiance = mix( hemiLight.groundColor, hemiLight.skyColor, hemiDiffuseWeight );
		return irradiance;
	}
#endif`,lights_toon_fragment:`ToonMaterial material;
material.diffuseColor = diffuseColor.rgb;`,lights_toon_pars_fragment:`varying vec3 vViewPosition;
struct ToonMaterial {
	vec3 diffuseColor;
};
void RE_Direct_Toon( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in ToonMaterial material, inout ReflectedLight reflectedLight ) {
	vec3 irradiance = getGradientIrradiance( geometryNormal, directLight.direction ) * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
void RE_IndirectDiffuse_Toon( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in ToonMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_Toon
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Toon`,lights_phong_fragment:`BlinnPhongMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.specularColor = specular;
material.specularShininess = shininess;
material.specularStrength = specularStrength;`,lights_phong_pars_fragment:`varying vec3 vViewPosition;
struct BlinnPhongMaterial {
	vec3 diffuseColor;
	vec3 specularColor;
	float specularShininess;
	float specularStrength;
};
void RE_Direct_BlinnPhong( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
	reflectedLight.directSpecular += irradiance * BRDF_BlinnPhong( directLight.direction, geometryViewDir, geometryNormal, material.specularColor, material.specularShininess ) * material.specularStrength;
}
void RE_IndirectDiffuse_BlinnPhong( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in BlinnPhongMaterial material, inout ReflectedLight reflectedLight ) {
	reflectedLight.indirectDiffuse += irradiance * BRDF_Lambert( material.diffuseColor );
}
#define RE_Direct				RE_Direct_BlinnPhong
#define RE_IndirectDiffuse		RE_IndirectDiffuse_BlinnPhong`,lights_physical_fragment:`PhysicalMaterial material;
material.diffuseColor = diffuseColor.rgb;
material.diffuseContribution = diffuseColor.rgb * ( 1.0 - metalnessFactor );
material.metalness = metalnessFactor;
vec3 dxy = max( abs( dFdx( nonPerturbedNormal ) ), abs( dFdy( nonPerturbedNormal ) ) );
float geometryRoughness = max( max( dxy.x, dxy.y ), dxy.z );
material.roughness = max( roughnessFactor, 0.0525 );material.roughness += geometryRoughness;
material.roughness = min( material.roughness, 1.0 );
#ifdef IOR
	material.ior = ior;
	#ifdef USE_SPECULAR
		float specularIntensityFactor = specularIntensity;
		vec3 specularColorFactor = specularColor;
		#ifdef USE_SPECULAR_COLORMAP
			specularColorFactor *= texture2D( specularColorMap, vSpecularColorMapUv ).rgb;
		#endif
		#ifdef USE_SPECULAR_INTENSITYMAP
			specularIntensityFactor *= texture2D( specularIntensityMap, vSpecularIntensityMapUv ).a;
		#endif
		material.specularF90 = mix( specularIntensityFactor, 1.0, metalnessFactor );
	#else
		float specularIntensityFactor = 1.0;
		vec3 specularColorFactor = vec3( 1.0 );
		material.specularF90 = 1.0;
	#endif
	material.specularColor = min( pow2( ( material.ior - 1.0 ) / ( material.ior + 1.0 ) ) * specularColorFactor, vec3( 1.0 ) ) * specularIntensityFactor;
	material.specularColorBlended = mix( material.specularColor, diffuseColor.rgb, metalnessFactor );
#else
	material.specularColor = vec3( 0.04 );
	material.specularColorBlended = mix( material.specularColor, diffuseColor.rgb, metalnessFactor );
	material.specularF90 = 1.0;
#endif
#ifdef USE_CLEARCOAT
	material.clearcoat = clearcoat;
	material.clearcoatRoughness = clearcoatRoughness;
	material.clearcoatF0 = vec3( 0.04 );
	material.clearcoatF90 = 1.0;
	#ifdef USE_CLEARCOATMAP
		material.clearcoat *= texture2D( clearcoatMap, vClearcoatMapUv ).x;
	#endif
	#ifdef USE_CLEARCOAT_ROUGHNESSMAP
		material.clearcoatRoughness *= texture2D( clearcoatRoughnessMap, vClearcoatRoughnessMapUv ).y;
	#endif
	material.clearcoat = saturate( material.clearcoat );	material.clearcoatRoughness = max( material.clearcoatRoughness, 0.0525 );
	material.clearcoatRoughness += geometryRoughness;
	material.clearcoatRoughness = min( material.clearcoatRoughness, 1.0 );
#endif
#ifdef USE_DISPERSION
	material.dispersion = dispersion;
#endif
#ifdef USE_IRIDESCENCE
	material.iridescence = iridescence;
	material.iridescenceIOR = iridescenceIOR;
	#ifdef USE_IRIDESCENCEMAP
		material.iridescence *= texture2D( iridescenceMap, vIridescenceMapUv ).r;
	#endif
	#ifdef USE_IRIDESCENCE_THICKNESSMAP
		material.iridescenceThickness = (iridescenceThicknessMaximum - iridescenceThicknessMinimum) * texture2D( iridescenceThicknessMap, vIridescenceThicknessMapUv ).g + iridescenceThicknessMinimum;
	#else
		material.iridescenceThickness = iridescenceThicknessMaximum;
	#endif
#endif
#ifdef USE_SHEEN
	material.sheenColor = sheenColor;
	#ifdef USE_SHEEN_COLORMAP
		material.sheenColor *= texture2D( sheenColorMap, vSheenColorMapUv ).rgb;
	#endif
	material.sheenRoughness = clamp( sheenRoughness, 0.0001, 1.0 );
	#ifdef USE_SHEEN_ROUGHNESSMAP
		material.sheenRoughness *= texture2D( sheenRoughnessMap, vSheenRoughnessMapUv ).a;
	#endif
#endif
#ifdef USE_ANISOTROPY
	#ifdef USE_ANISOTROPYMAP
		mat2 anisotropyMat = mat2( anisotropyVector.x, anisotropyVector.y, - anisotropyVector.y, anisotropyVector.x );
		vec3 anisotropyPolar = texture2D( anisotropyMap, vAnisotropyMapUv ).rgb;
		vec2 anisotropyV = anisotropyMat * normalize( 2.0 * anisotropyPolar.rg - vec2( 1.0 ) ) * anisotropyPolar.b;
	#else
		vec2 anisotropyV = anisotropyVector;
	#endif
	material.anisotropy = length( anisotropyV );
	if( material.anisotropy == 0.0 ) {
		anisotropyV = vec2( 1.0, 0.0 );
	} else {
		anisotropyV /= material.anisotropy;
		material.anisotropy = saturate( material.anisotropy );
	}
	material.alphaT = mix( pow2( material.roughness ), 1.0, pow2( material.anisotropy ) );
	material.anisotropyT = tbn[ 0 ] * anisotropyV.x + tbn[ 1 ] * anisotropyV.y;
	material.anisotropyB = tbn[ 1 ] * anisotropyV.x - tbn[ 0 ] * anisotropyV.y;
#endif`,lights_physical_pars_fragment:`uniform sampler2D dfgLUT;
struct PhysicalMaterial {
	vec3 diffuseColor;
	vec3 diffuseContribution;
	vec3 specularColor;
	vec3 specularColorBlended;
	float roughness;
	float metalness;
	float specularF90;
	float dispersion;
	#ifdef USE_CLEARCOAT
		float clearcoat;
		float clearcoatRoughness;
		vec3 clearcoatF0;
		float clearcoatF90;
	#endif
	#ifdef USE_IRIDESCENCE
		float iridescence;
		float iridescenceIOR;
		float iridescenceThickness;
		vec3 iridescenceFresnel;
		vec3 iridescenceF0;
		vec3 iridescenceFresnelDielectric;
		vec3 iridescenceFresnelMetallic;
	#endif
	#ifdef USE_SHEEN
		vec3 sheenColor;
		float sheenRoughness;
	#endif
	#ifdef IOR
		float ior;
	#endif
	#ifdef USE_TRANSMISSION
		float transmission;
		float transmissionAlpha;
		float thickness;
		float attenuationDistance;
		vec3 attenuationColor;
	#endif
	#ifdef USE_ANISOTROPY
		float anisotropy;
		float alphaT;
		vec3 anisotropyT;
		vec3 anisotropyB;
	#endif
};
vec3 clearcoatSpecularDirect = vec3( 0.0 );
vec3 clearcoatSpecularIndirect = vec3( 0.0 );
vec3 sheenSpecularDirect = vec3( 0.0 );
vec3 sheenSpecularIndirect = vec3(0.0 );
vec3 Schlick_to_F0( const in vec3 f, const in float f90, const in float dotVH ) {
    float x = clamp( 1.0 - dotVH, 0.0, 1.0 );
    float x2 = x * x;
    float x5 = clamp( x * x2 * x2, 0.0, 0.9999 );
    return ( f - vec3( f90 ) * x5 ) / ( 1.0 - x5 );
}
float V_GGX_SmithCorrelated( const in float alpha, const in float dotNL, const in float dotNV ) {
	float a2 = pow2( alpha );
	float gv = dotNL * sqrt( a2 + ( 1.0 - a2 ) * pow2( dotNV ) );
	float gl = dotNV * sqrt( a2 + ( 1.0 - a2 ) * pow2( dotNL ) );
	return 0.5 / max( gv + gl, EPSILON );
}
float D_GGX( const in float alpha, const in float dotNH ) {
	float a2 = pow2( alpha );
	float denom = pow2( dotNH ) * ( a2 - 1.0 ) + 1.0;
	return RECIPROCAL_PI * a2 / pow2( denom );
}
#ifdef USE_ANISOTROPY
	float V_GGX_SmithCorrelated_Anisotropic( const in float alphaT, const in float alphaB, const in float dotTV, const in float dotBV, const in float dotTL, const in float dotBL, const in float dotNV, const in float dotNL ) {
		float gv = dotNL * length( vec3( alphaT * dotTV, alphaB * dotBV, dotNV ) );
		float gl = dotNV * length( vec3( alphaT * dotTL, alphaB * dotBL, dotNL ) );
		float v = 0.5 / ( gv + gl );
		return v;
	}
	float D_GGX_Anisotropic( const in float alphaT, const in float alphaB, const in float dotNH, const in float dotTH, const in float dotBH ) {
		float a2 = alphaT * alphaB;
		highp vec3 v = vec3( alphaB * dotTH, alphaT * dotBH, a2 * dotNH );
		highp float v2 = dot( v, v );
		float w2 = a2 / v2;
		return RECIPROCAL_PI * a2 * pow2 ( w2 );
	}
#endif
#ifdef USE_CLEARCOAT
	vec3 BRDF_GGX_Clearcoat( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material) {
		vec3 f0 = material.clearcoatF0;
		float f90 = material.clearcoatF90;
		float roughness = material.clearcoatRoughness;
		float alpha = pow2( roughness );
		vec3 halfDir = normalize( lightDir + viewDir );
		float dotNL = saturate( dot( normal, lightDir ) );
		float dotNV = saturate( dot( normal, viewDir ) );
		float dotNH = saturate( dot( normal, halfDir ) );
		float dotVH = saturate( dot( viewDir, halfDir ) );
		vec3 F = F_Schlick( f0, f90, dotVH );
		float V = V_GGX_SmithCorrelated( alpha, dotNL, dotNV );
		float D = D_GGX( alpha, dotNH );
		return F * ( V * D );
	}
#endif
vec3 BRDF_GGX( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material ) {
	vec3 f0 = material.specularColorBlended;
	float f90 = material.specularF90;
	float roughness = material.roughness;
	float alpha = pow2( roughness );
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	float dotNH = saturate( dot( normal, halfDir ) );
	float dotVH = saturate( dot( viewDir, halfDir ) );
	vec3 F = F_Schlick( f0, f90, dotVH );
	#ifdef USE_IRIDESCENCE
		F = mix( F, material.iridescenceFresnel, material.iridescence );
	#endif
	#ifdef USE_ANISOTROPY
		float dotTL = dot( material.anisotropyT, lightDir );
		float dotTV = dot( material.anisotropyT, viewDir );
		float dotTH = dot( material.anisotropyT, halfDir );
		float dotBL = dot( material.anisotropyB, lightDir );
		float dotBV = dot( material.anisotropyB, viewDir );
		float dotBH = dot( material.anisotropyB, halfDir );
		float V = V_GGX_SmithCorrelated_Anisotropic( material.alphaT, alpha, dotTV, dotBV, dotTL, dotBL, dotNV, dotNL );
		float D = D_GGX_Anisotropic( material.alphaT, alpha, dotNH, dotTH, dotBH );
	#else
		float V = V_GGX_SmithCorrelated( alpha, dotNL, dotNV );
		float D = D_GGX( alpha, dotNH );
	#endif
	return F * ( V * D );
}
vec2 LTC_Uv( const in vec3 N, const in vec3 V, const in float roughness ) {
	const float LUT_SIZE = 64.0;
	const float LUT_SCALE = ( LUT_SIZE - 1.0 ) / LUT_SIZE;
	const float LUT_BIAS = 0.5 / LUT_SIZE;
	float dotNV = saturate( dot( N, V ) );
	vec2 uv = vec2( roughness, sqrt( 1.0 - dotNV ) );
	uv = uv * LUT_SCALE + LUT_BIAS;
	return uv;
}
float LTC_ClippedSphereFormFactor( const in vec3 f ) {
	float l = length( f );
	return max( ( l * l + f.z ) / ( l + 1.0 ), 0.0 );
}
vec3 LTC_EdgeVectorFormFactor( const in vec3 v1, const in vec3 v2 ) {
	float x = dot( v1, v2 );
	float y = abs( x );
	float a = 0.8543985 + ( 0.4965155 + 0.0145206 * y ) * y;
	float b = 3.4175940 + ( 4.1616724 + y ) * y;
	float v = a / b;
	float theta_sintheta = ( x > 0.0 ) ? v : 0.5 * inversesqrt( max( 1.0 - x * x, 1e-7 ) ) - v;
	return cross( v1, v2 ) * theta_sintheta;
}
vec3 LTC_Evaluate( const in vec3 N, const in vec3 V, const in vec3 P, const in mat3 mInv, const in vec3 rectCoords[ 4 ] ) {
	vec3 v1 = rectCoords[ 1 ] - rectCoords[ 0 ];
	vec3 v2 = rectCoords[ 3 ] - rectCoords[ 0 ];
	vec3 lightNormal = cross( v1, v2 );
	if( dot( lightNormal, P - rectCoords[ 0 ] ) < 0.0 ) return vec3( 0.0 );
	vec3 T1, T2;
	T1 = normalize( V - N * dot( V, N ) );
	T2 = - cross( N, T1 );
	mat3 mat = mInv * transpose( mat3( T1, T2, N ) );
	vec3 coords[ 4 ];
	coords[ 0 ] = mat * ( rectCoords[ 0 ] - P );
	coords[ 1 ] = mat * ( rectCoords[ 1 ] - P );
	coords[ 2 ] = mat * ( rectCoords[ 2 ] - P );
	coords[ 3 ] = mat * ( rectCoords[ 3 ] - P );
	coords[ 0 ] = normalize( coords[ 0 ] );
	coords[ 1 ] = normalize( coords[ 1 ] );
	coords[ 2 ] = normalize( coords[ 2 ] );
	coords[ 3 ] = normalize( coords[ 3 ] );
	vec3 vectorFormFactor = vec3( 0.0 );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 0 ], coords[ 1 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 1 ], coords[ 2 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 2 ], coords[ 3 ] );
	vectorFormFactor += LTC_EdgeVectorFormFactor( coords[ 3 ], coords[ 0 ] );
	float result = LTC_ClippedSphereFormFactor( vectorFormFactor );
	return vec3( result );
}
#if defined( USE_SHEEN )
float D_Charlie( float roughness, float dotNH ) {
	float alpha = pow2( roughness );
	float invAlpha = 1.0 / alpha;
	float cos2h = dotNH * dotNH;
	float sin2h = max( 1.0 - cos2h, 0.0078125 );
	return ( 2.0 + invAlpha ) * pow( sin2h, invAlpha * 0.5 ) / ( 2.0 * PI );
}
float V_Neubelt( float dotNV, float dotNL ) {
	return saturate( 1.0 / ( 4.0 * ( dotNL + dotNV - dotNL * dotNV ) ) );
}
vec3 BRDF_Sheen( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, vec3 sheenColor, const in float sheenRoughness ) {
	vec3 halfDir = normalize( lightDir + viewDir );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	float dotNH = saturate( dot( normal, halfDir ) );
	float D = D_Charlie( sheenRoughness, dotNH );
	float V = V_Neubelt( dotNV, dotNL );
	return sheenColor * ( D * V );
}
#endif
float IBLSheenBRDF( const in vec3 normal, const in vec3 viewDir, const in float roughness ) {
	float dotNV = saturate( dot( normal, viewDir ) );
	float r2 = roughness * roughness;
	float rInv = 1.0 / ( roughness + 0.1 );
	float a = -1.9362 + 1.0678 * roughness + 0.4573 * r2 - 0.8469 * rInv;
	float b = -0.6014 + 0.5538 * roughness - 0.4670 * r2 - 0.1255 * rInv;
	float DG = exp( a * dotNV + b );
	return saturate( DG );
}
vec3 EnvironmentBRDF( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float roughness ) {
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 fab = texture2D( dfgLUT, vec2( roughness, dotNV ) ).rg;
	return specularColor * fab.x + specularF90 * fab.y;
}
#ifdef USE_IRIDESCENCE
void computeMultiscatteringIridescence( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float iridescence, const in vec3 iridescenceF0, const in float roughness, inout vec3 singleScatter, inout vec3 multiScatter ) {
#else
void computeMultiscattering( const in vec3 normal, const in vec3 viewDir, const in vec3 specularColor, const in float specularF90, const in float roughness, inout vec3 singleScatter, inout vec3 multiScatter ) {
#endif
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 fab = texture2D( dfgLUT, vec2( roughness, dotNV ) ).rg;
	#ifdef USE_IRIDESCENCE
		vec3 Fr = mix( specularColor, iridescenceF0, iridescence );
	#else
		vec3 Fr = specularColor;
	#endif
	vec3 FssEss = Fr * fab.x + specularF90 * fab.y;
	float Ess = fab.x + fab.y;
	float Ems = 1.0 - Ess;
	vec3 Favg = Fr + ( 1.0 - Fr ) * 0.047619;	vec3 Fms = FssEss * Favg / ( 1.0 - Ems * Favg );
	singleScatter += FssEss;
	multiScatter += Fms * Ems;
}
vec3 BRDF_GGX_Multiscatter( const in vec3 lightDir, const in vec3 viewDir, const in vec3 normal, const in PhysicalMaterial material ) {
	vec3 singleScatter = BRDF_GGX( lightDir, viewDir, normal, material );
	float dotNL = saturate( dot( normal, lightDir ) );
	float dotNV = saturate( dot( normal, viewDir ) );
	vec2 dfgV = texture2D( dfgLUT, vec2( material.roughness, dotNV ) ).rg;
	vec2 dfgL = texture2D( dfgLUT, vec2( material.roughness, dotNL ) ).rg;
	vec3 FssEss_V = material.specularColorBlended * dfgV.x + material.specularF90 * dfgV.y;
	vec3 FssEss_L = material.specularColorBlended * dfgL.x + material.specularF90 * dfgL.y;
	float Ess_V = dfgV.x + dfgV.y;
	float Ess_L = dfgL.x + dfgL.y;
	float Ems_V = 1.0 - Ess_V;
	float Ems_L = 1.0 - Ess_L;
	vec3 Favg = material.specularColorBlended + ( 1.0 - material.specularColorBlended ) * 0.047619;
	vec3 Fms = FssEss_V * FssEss_L * Favg / ( 1.0 - Ems_V * Ems_L * Favg + EPSILON );
	float compensationFactor = Ems_V * Ems_L;
	vec3 multiScatter = Fms * compensationFactor;
	return singleScatter + multiScatter;
}
#if NUM_RECT_AREA_LIGHTS > 0
	void RE_Direct_RectArea_Physical( const in RectAreaLight rectAreaLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
		vec3 normal = geometryNormal;
		vec3 viewDir = geometryViewDir;
		vec3 position = geometryPosition;
		vec3 lightPos = rectAreaLight.position;
		vec3 halfWidth = rectAreaLight.halfWidth;
		vec3 halfHeight = rectAreaLight.halfHeight;
		vec3 lightColor = rectAreaLight.color;
		float roughness = material.roughness;
		vec3 rectCoords[ 4 ];
		rectCoords[ 0 ] = lightPos + halfWidth - halfHeight;		rectCoords[ 1 ] = lightPos - halfWidth - halfHeight;
		rectCoords[ 2 ] = lightPos - halfWidth + halfHeight;
		rectCoords[ 3 ] = lightPos + halfWidth + halfHeight;
		vec2 uv = LTC_Uv( normal, viewDir, roughness );
		vec4 t1 = texture2D( ltc_1, uv );
		vec4 t2 = texture2D( ltc_2, uv );
		mat3 mInv = mat3(
			vec3( t1.x, 0, t1.y ),
			vec3(    0, 1,    0 ),
			vec3( t1.z, 0, t1.w )
		);
		vec3 fresnel = ( material.specularColorBlended * t2.x + ( material.specularF90 - material.specularColorBlended ) * t2.y );
		reflectedLight.directSpecular += lightColor * fresnel * LTC_Evaluate( normal, viewDir, position, mInv, rectCoords );
		reflectedLight.directDiffuse += lightColor * material.diffuseContribution * LTC_Evaluate( normal, viewDir, position, mat3( 1.0 ), rectCoords );
		#ifdef USE_CLEARCOAT
			vec3 Ncc = geometryClearcoatNormal;
			vec2 uvClearcoat = LTC_Uv( Ncc, viewDir, material.clearcoatRoughness );
			vec4 t1Clearcoat = texture2D( ltc_1, uvClearcoat );
			vec4 t2Clearcoat = texture2D( ltc_2, uvClearcoat );
			mat3 mInvClearcoat = mat3(
				vec3( t1Clearcoat.x, 0, t1Clearcoat.y ),
				vec3(             0, 1,             0 ),
				vec3( t1Clearcoat.z, 0, t1Clearcoat.w )
			);
			vec3 fresnelClearcoat = material.clearcoatF0 * t2Clearcoat.x + ( material.clearcoatF90 - material.clearcoatF0 ) * t2Clearcoat.y;
			clearcoatSpecularDirect += lightColor * fresnelClearcoat * LTC_Evaluate( Ncc, viewDir, position, mInvClearcoat, rectCoords );
		#endif
	}
#endif
void RE_Direct_Physical( const in IncidentLight directLight, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	float dotNL = saturate( dot( geometryNormal, directLight.direction ) );
	vec3 irradiance = dotNL * directLight.color;
	#ifdef USE_CLEARCOAT
		float dotNLcc = saturate( dot( geometryClearcoatNormal, directLight.direction ) );
		vec3 ccIrradiance = dotNLcc * directLight.color;
		clearcoatSpecularDirect += ccIrradiance * BRDF_GGX_Clearcoat( directLight.direction, geometryViewDir, geometryClearcoatNormal, material );
	#endif
	#ifdef USE_SHEEN
 
 		sheenSpecularDirect += irradiance * BRDF_Sheen( directLight.direction, geometryViewDir, geometryNormal, material.sheenColor, material.sheenRoughness );
 
 		float sheenAlbedoV = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
 		float sheenAlbedoL = IBLSheenBRDF( geometryNormal, directLight.direction, material.sheenRoughness );
 
 		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * max( sheenAlbedoV, sheenAlbedoL );
 
 		irradiance *= sheenEnergyComp;
 
 	#endif
	reflectedLight.directSpecular += irradiance * BRDF_GGX_Multiscatter( directLight.direction, geometryViewDir, geometryNormal, material );
	reflectedLight.directDiffuse += irradiance * BRDF_Lambert( material.diffuseContribution );
}
void RE_IndirectDiffuse_Physical( const in vec3 irradiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight ) {
	vec3 diffuse = irradiance * BRDF_Lambert( material.diffuseContribution );
	#ifdef USE_SHEEN
		float sheenAlbedo = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * sheenAlbedo;
		diffuse *= sheenEnergyComp;
	#endif
	reflectedLight.indirectDiffuse += diffuse;
}
void RE_IndirectSpecular_Physical( const in vec3 radiance, const in vec3 irradiance, const in vec3 clearcoatRadiance, const in vec3 geometryPosition, const in vec3 geometryNormal, const in vec3 geometryViewDir, const in vec3 geometryClearcoatNormal, const in PhysicalMaterial material, inout ReflectedLight reflectedLight) {
	#ifdef USE_CLEARCOAT
		clearcoatSpecularIndirect += clearcoatRadiance * EnvironmentBRDF( geometryClearcoatNormal, geometryViewDir, material.clearcoatF0, material.clearcoatF90, material.clearcoatRoughness );
	#endif
	#ifdef USE_SHEEN
		sheenSpecularIndirect += irradiance * material.sheenColor * IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness ) * RECIPROCAL_PI;
 	#endif
	vec3 singleScatteringDielectric = vec3( 0.0 );
	vec3 multiScatteringDielectric = vec3( 0.0 );
	vec3 singleScatteringMetallic = vec3( 0.0 );
	vec3 multiScatteringMetallic = vec3( 0.0 );
	#ifdef USE_IRIDESCENCE
		computeMultiscatteringIridescence( geometryNormal, geometryViewDir, material.specularColor, material.specularF90, material.iridescence, material.iridescenceFresnelDielectric, material.roughness, singleScatteringDielectric, multiScatteringDielectric );
		computeMultiscatteringIridescence( geometryNormal, geometryViewDir, material.diffuseColor, material.specularF90, material.iridescence, material.iridescenceFresnelMetallic, material.roughness, singleScatteringMetallic, multiScatteringMetallic );
	#else
		computeMultiscattering( geometryNormal, geometryViewDir, material.specularColor, material.specularF90, material.roughness, singleScatteringDielectric, multiScatteringDielectric );
		computeMultiscattering( geometryNormal, geometryViewDir, material.diffuseColor, material.specularF90, material.roughness, singleScatteringMetallic, multiScatteringMetallic );
	#endif
	vec3 singleScattering = mix( singleScatteringDielectric, singleScatteringMetallic, material.metalness );
	vec3 multiScattering = mix( multiScatteringDielectric, multiScatteringMetallic, material.metalness );
	vec3 totalScatteringDielectric = singleScatteringDielectric + multiScatteringDielectric;
	vec3 diffuse = material.diffuseContribution * ( 1.0 - totalScatteringDielectric );
	vec3 cosineWeightedIrradiance = irradiance * RECIPROCAL_PI;
	vec3 indirectSpecular = radiance * singleScattering;
	indirectSpecular += multiScattering * cosineWeightedIrradiance;
	vec3 indirectDiffuse = diffuse * cosineWeightedIrradiance;
	#ifdef USE_SHEEN
		float sheenAlbedo = IBLSheenBRDF( geometryNormal, geometryViewDir, material.sheenRoughness );
		float sheenEnergyComp = 1.0 - max3( material.sheenColor ) * sheenAlbedo;
		indirectSpecular *= sheenEnergyComp;
		indirectDiffuse *= sheenEnergyComp;
	#endif
	reflectedLight.indirectSpecular += indirectSpecular;
	reflectedLight.indirectDiffuse += indirectDiffuse;
}
#define RE_Direct				RE_Direct_Physical
#define RE_Direct_RectArea		RE_Direct_RectArea_Physical
#define RE_IndirectDiffuse		RE_IndirectDiffuse_Physical
#define RE_IndirectSpecular		RE_IndirectSpecular_Physical
float computeSpecularOcclusion( const in float dotNV, const in float ambientOcclusion, const in float roughness ) {
	return saturate( pow( dotNV + ambientOcclusion, exp2( - 16.0 * roughness - 1.0 ) ) - 1.0 + ambientOcclusion );
}`,lights_fragment_begin:`
vec3 geometryPosition = - vViewPosition;
vec3 geometryNormal = normal;
vec3 geometryViewDir = ( isOrthographic ) ? vec3( 0, 0, 1 ) : normalize( vViewPosition );
vec3 geometryClearcoatNormal = vec3( 0.0 );
#ifdef USE_CLEARCOAT
	geometryClearcoatNormal = clearcoatNormal;
#endif
#ifdef USE_IRIDESCENCE
	float dotNVi = saturate( dot( normal, geometryViewDir ) );
	if ( material.iridescenceThickness == 0.0 ) {
		material.iridescence = 0.0;
	} else {
		material.iridescence = saturate( material.iridescence );
	}
	if ( material.iridescence > 0.0 ) {
		material.iridescenceFresnelDielectric = evalIridescence( 1.0, material.iridescenceIOR, dotNVi, material.iridescenceThickness, material.specularColor );
		material.iridescenceFresnelMetallic = evalIridescence( 1.0, material.iridescenceIOR, dotNVi, material.iridescenceThickness, material.diffuseColor );
		material.iridescenceFresnel = mix( material.iridescenceFresnelDielectric, material.iridescenceFresnelMetallic, material.metalness );
		material.iridescenceF0 = Schlick_to_F0( material.iridescenceFresnel, 1.0, dotNVi );
	}
#endif
IncidentLight directLight;
#if ( NUM_POINT_LIGHTS > 0 ) && defined( RE_Direct )
	PointLight pointLight;
	#if defined( USE_SHADOWMAP ) && NUM_POINT_LIGHT_SHADOWS > 0
	PointLightShadow pointLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_POINT_LIGHTS; i ++ ) {
		pointLight = pointLights[ i ];
		getPointLightInfo( pointLight, geometryPosition, directLight );
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_POINT_LIGHT_SHADOWS ) && ( defined( SHADOWMAP_TYPE_PCF ) || defined( SHADOWMAP_TYPE_BASIC ) )
		pointLightShadow = pointLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getPointShadow( pointShadowMap[ i ], pointLightShadow.shadowMapSize, pointLightShadow.shadowIntensity, pointLightShadow.shadowBias, pointLightShadow.shadowRadius, vPointShadowCoord[ i ], pointLightShadow.shadowCameraNear, pointLightShadow.shadowCameraFar ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_SPOT_LIGHTS > 0 ) && defined( RE_Direct )
	SpotLight spotLight;
	vec4 spotColor;
	vec3 spotLightCoord;
	bool inSpotLightMap;
	#if defined( USE_SHADOWMAP ) && NUM_SPOT_LIGHT_SHADOWS > 0
	SpotLightShadow spotLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHTS; i ++ ) {
		spotLight = spotLights[ i ];
		getSpotLightInfo( spotLight, geometryPosition, directLight );
		#if ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS )
		#define SPOT_LIGHT_MAP_INDEX UNROLLED_LOOP_INDEX
		#elif ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
		#define SPOT_LIGHT_MAP_INDEX NUM_SPOT_LIGHT_MAPS
		#else
		#define SPOT_LIGHT_MAP_INDEX ( UNROLLED_LOOP_INDEX - NUM_SPOT_LIGHT_SHADOWS + NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS )
		#endif
		#if ( SPOT_LIGHT_MAP_INDEX < NUM_SPOT_LIGHT_MAPS )
			spotLightCoord = vSpotLightCoord[ i ].xyz / vSpotLightCoord[ i ].w;
			inSpotLightMap = all( lessThan( abs( spotLightCoord * 2. - 1. ), vec3( 1.0 ) ) );
			spotColor = texture2D( spotLightMap[ SPOT_LIGHT_MAP_INDEX ], spotLightCoord.xy );
			directLight.color = inSpotLightMap ? directLight.color * spotColor.rgb : directLight.color;
		#endif
		#undef SPOT_LIGHT_MAP_INDEX
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
		spotLightShadow = spotLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getShadow( spotShadowMap[ i ], spotLightShadow.shadowMapSize, spotLightShadow.shadowIntensity, spotLightShadow.shadowBias, spotLightShadow.shadowRadius, vSpotLightCoord[ i ] ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_DIR_LIGHTS > 0 ) && defined( RE_Direct )
	DirectionalLight directionalLight;
	#if defined( USE_SHADOWMAP ) && NUM_DIR_LIGHT_SHADOWS > 0
	DirectionalLightShadow directionalLightShadow;
	#endif
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_DIR_LIGHTS; i ++ ) {
		directionalLight = directionalLights[ i ];
		getDirectionalLightInfo( directionalLight, directLight );
		#if defined( USE_SHADOWMAP ) && ( UNROLLED_LOOP_INDEX < NUM_DIR_LIGHT_SHADOWS )
		directionalLightShadow = directionalLightShadows[ i ];
		directLight.color *= ( directLight.visible && receiveShadow ) ? getShadow( directionalShadowMap[ i ], directionalLightShadow.shadowMapSize, directionalLightShadow.shadowIntensity, directionalLightShadow.shadowBias, directionalLightShadow.shadowRadius, vDirectionalShadowCoord[ i ] ) : 1.0;
		#endif
		RE_Direct( directLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if ( NUM_RECT_AREA_LIGHTS > 0 ) && defined( RE_Direct_RectArea )
	RectAreaLight rectAreaLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_RECT_AREA_LIGHTS; i ++ ) {
		rectAreaLight = rectAreaLights[ i ];
		RE_Direct_RectArea( rectAreaLight, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
	}
	#pragma unroll_loop_end
#endif
#if defined( RE_IndirectDiffuse )
	vec3 iblIrradiance = vec3( 0.0 );
	vec3 irradiance = getAmbientLightIrradiance( ambientLightColor );
	#if defined( USE_LIGHT_PROBES )
		irradiance += getLightProbeIrradiance( lightProbe, geometryNormal );
	#endif
	#if ( NUM_HEMI_LIGHTS > 0 )
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_HEMI_LIGHTS; i ++ ) {
			irradiance += getHemisphereLightIrradiance( hemisphereLights[ i ], geometryNormal );
		}
		#pragma unroll_loop_end
	#endif
#endif
#if defined( RE_IndirectSpecular )
	vec3 radiance = vec3( 0.0 );
	vec3 clearcoatRadiance = vec3( 0.0 );
#endif`,lights_fragment_maps:`#if defined( RE_IndirectDiffuse )
	#ifdef USE_LIGHTMAP
		vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );
		vec3 lightMapIrradiance = lightMapTexel.rgb * lightMapIntensity;
		irradiance += lightMapIrradiance;
	#endif
	#if defined( USE_ENVMAP ) && defined( ENVMAP_TYPE_CUBE_UV )
		#if defined( STANDARD ) || defined( LAMBERT ) || defined( PHONG )
			iblIrradiance += getIBLIrradiance( geometryNormal );
		#endif
	#endif
#endif
#if defined( USE_ENVMAP ) && defined( RE_IndirectSpecular )
	#ifdef USE_ANISOTROPY
		radiance += getIBLAnisotropyRadiance( geometryViewDir, geometryNormal, material.roughness, material.anisotropyB, material.anisotropy );
	#else
		radiance += getIBLRadiance( geometryViewDir, geometryNormal, material.roughness );
	#endif
	#ifdef USE_CLEARCOAT
		clearcoatRadiance += getIBLRadiance( geometryViewDir, geometryClearcoatNormal, material.clearcoatRoughness );
	#endif
#endif`,lights_fragment_end:`#if defined( RE_IndirectDiffuse )
	#if defined( LAMBERT ) || defined( PHONG )
		irradiance += iblIrradiance;
	#endif
	RE_IndirectDiffuse( irradiance, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
#endif
#if defined( RE_IndirectSpecular )
	RE_IndirectSpecular( radiance, iblIrradiance, clearcoatRadiance, geometryPosition, geometryNormal, geometryViewDir, geometryClearcoatNormal, material, reflectedLight );
#endif`,logdepthbuf_fragment:`#if defined( USE_LOGARITHMIC_DEPTH_BUFFER )
	gl_FragDepth = vIsPerspective == 0.0 ? gl_FragCoord.z : log2( vFragDepth ) * logDepthBufFC * 0.5;
#endif`,logdepthbuf_pars_fragment:`#if defined( USE_LOGARITHMIC_DEPTH_BUFFER )
	uniform float logDepthBufFC;
	varying float vFragDepth;
	varying float vIsPerspective;
#endif`,logdepthbuf_pars_vertex:`#ifdef USE_LOGARITHMIC_DEPTH_BUFFER
	varying float vFragDepth;
	varying float vIsPerspective;
#endif`,logdepthbuf_vertex:`#ifdef USE_LOGARITHMIC_DEPTH_BUFFER
	vFragDepth = 1.0 + gl_Position.w;
	vIsPerspective = float( isPerspectiveMatrix( projectionMatrix ) );
#endif`,map_fragment:`#ifdef USE_MAP
	vec4 sampledDiffuseColor = texture2D( map, vMapUv );
	#ifdef DECODE_VIDEO_TEXTURE
		sampledDiffuseColor = sRGBTransferEOTF( sampledDiffuseColor );
	#endif
	diffuseColor *= sampledDiffuseColor;
#endif`,map_pars_fragment:`#ifdef USE_MAP
	uniform sampler2D map;
#endif`,map_particle_fragment:`#if defined( USE_MAP ) || defined( USE_ALPHAMAP )
	#if defined( USE_POINTS_UV )
		vec2 uv = vUv;
	#else
		vec2 uv = ( uvTransform * vec3( gl_PointCoord.x, 1.0 - gl_PointCoord.y, 1 ) ).xy;
	#endif
#endif
#ifdef USE_MAP
	diffuseColor *= texture2D( map, uv );
#endif
#ifdef USE_ALPHAMAP
	diffuseColor.a *= texture2D( alphaMap, uv ).g;
#endif`,map_particle_pars_fragment:`#if defined( USE_POINTS_UV )
	varying vec2 vUv;
#else
	#if defined( USE_MAP ) || defined( USE_ALPHAMAP )
		uniform mat3 uvTransform;
	#endif
#endif
#ifdef USE_MAP
	uniform sampler2D map;
#endif
#ifdef USE_ALPHAMAP
	uniform sampler2D alphaMap;
#endif`,metalnessmap_fragment:`float metalnessFactor = metalness;
#ifdef USE_METALNESSMAP
	vec4 texelMetalness = texture2D( metalnessMap, vMetalnessMapUv );
	metalnessFactor *= texelMetalness.b;
#endif`,metalnessmap_pars_fragment:`#ifdef USE_METALNESSMAP
	uniform sampler2D metalnessMap;
#endif`,morphinstance_vertex:`#ifdef USE_INSTANCING_MORPH
	float morphTargetInfluences[ MORPHTARGETS_COUNT ];
	float morphTargetBaseInfluence = texelFetch( morphTexture, ivec2( 0, gl_InstanceID ), 0 ).r;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		morphTargetInfluences[i] =  texelFetch( morphTexture, ivec2( i + 1, gl_InstanceID ), 0 ).r;
	}
#endif`,morphcolor_vertex:`#if defined( USE_MORPHCOLORS )
	vColor *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		#if defined( USE_COLOR_ALPHA )
			if ( morphTargetInfluences[ i ] != 0.0 ) vColor += getMorph( gl_VertexID, i, 2 ) * morphTargetInfluences[ i ];
		#elif defined( USE_COLOR )
			if ( morphTargetInfluences[ i ] != 0.0 ) vColor += getMorph( gl_VertexID, i, 2 ).rgb * morphTargetInfluences[ i ];
		#endif
	}
#endif`,morphnormal_vertex:`#ifdef USE_MORPHNORMALS
	objectNormal *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		if ( morphTargetInfluences[ i ] != 0.0 ) objectNormal += getMorph( gl_VertexID, i, 1 ).xyz * morphTargetInfluences[ i ];
	}
#endif`,morphtarget_pars_vertex:`#ifdef USE_MORPHTARGETS
	#ifndef USE_INSTANCING_MORPH
		uniform float morphTargetBaseInfluence;
		uniform float morphTargetInfluences[ MORPHTARGETS_COUNT ];
	#endif
	uniform sampler2DArray morphTargetsTexture;
	uniform ivec2 morphTargetsTextureSize;
	vec4 getMorph( const in int vertexIndex, const in int morphTargetIndex, const in int offset ) {
		int texelIndex = vertexIndex * MORPHTARGETS_TEXTURE_STRIDE + offset;
		int y = texelIndex / morphTargetsTextureSize.x;
		int x = texelIndex - y * morphTargetsTextureSize.x;
		ivec3 morphUV = ivec3( x, y, morphTargetIndex );
		return texelFetch( morphTargetsTexture, morphUV, 0 );
	}
#endif`,morphtarget_vertex:`#ifdef USE_MORPHTARGETS
	transformed *= morphTargetBaseInfluence;
	for ( int i = 0; i < MORPHTARGETS_COUNT; i ++ ) {
		if ( morphTargetInfluences[ i ] != 0.0 ) transformed += getMorph( gl_VertexID, i, 0 ).xyz * morphTargetInfluences[ i ];
	}
#endif`,normal_fragment_begin:`float faceDirection = gl_FrontFacing ? 1.0 : - 1.0;
#ifdef FLAT_SHADED
	vec3 fdx = dFdx( vViewPosition );
	vec3 fdy = dFdy( vViewPosition );
	vec3 normal = normalize( cross( fdx, fdy ) );
#else
	vec3 normal = normalize( vNormal );
	#ifdef DOUBLE_SIDED
		normal *= faceDirection;
	#endif
#endif
#if defined( USE_NORMALMAP_TANGENTSPACE ) || defined( USE_CLEARCOAT_NORMALMAP ) || defined( USE_ANISOTROPY )
	#ifdef USE_TANGENT
		mat3 tbn = mat3( normalize( vTangent ), normalize( vBitangent ), normal );
	#else
		mat3 tbn = getTangentFrame( - vViewPosition, normal,
		#if defined( USE_NORMALMAP )
			vNormalMapUv
		#elif defined( USE_CLEARCOAT_NORMALMAP )
			vClearcoatNormalMapUv
		#else
			vUv
		#endif
		);
	#endif
	#if defined( DOUBLE_SIDED ) && ! defined( FLAT_SHADED )
		tbn[0] *= faceDirection;
		tbn[1] *= faceDirection;
	#endif
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	#ifdef USE_TANGENT
		mat3 tbn2 = mat3( normalize( vTangent ), normalize( vBitangent ), normal );
	#else
		mat3 tbn2 = getTangentFrame( - vViewPosition, normal, vClearcoatNormalMapUv );
	#endif
	#if defined( DOUBLE_SIDED ) && ! defined( FLAT_SHADED )
		tbn2[0] *= faceDirection;
		tbn2[1] *= faceDirection;
	#endif
#endif
vec3 nonPerturbedNormal = normal;`,normal_fragment_maps:`#ifdef USE_NORMALMAP_OBJECTSPACE
	normal = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;
	#ifdef FLIP_SIDED
		normal = - normal;
	#endif
	#ifdef DOUBLE_SIDED
		normal = normal * faceDirection;
	#endif
	normal = normalize( normalMatrix * normal );
#elif defined( USE_NORMALMAP_TANGENTSPACE )
	vec3 mapN = texture2D( normalMap, vNormalMapUv ).xyz * 2.0 - 1.0;
	mapN.xy *= normalScale;
	normal = normalize( tbn * mapN );
#elif defined( USE_BUMPMAP )
	normal = perturbNormalArb( - vViewPosition, normal, dHdxy_fwd(), faceDirection );
#endif`,normal_pars_fragment:`#ifndef FLAT_SHADED
	varying vec3 vNormal;
	#ifdef USE_TANGENT
		varying vec3 vTangent;
		varying vec3 vBitangent;
	#endif
#endif`,normal_pars_vertex:`#ifndef FLAT_SHADED
	varying vec3 vNormal;
	#ifdef USE_TANGENT
		varying vec3 vTangent;
		varying vec3 vBitangent;
	#endif
#endif`,normal_vertex:`#ifndef FLAT_SHADED
	vNormal = normalize( transformedNormal );
	#ifdef USE_TANGENT
		vTangent = normalize( transformedTangent );
		vBitangent = normalize( cross( vNormal, vTangent ) * tangent.w );
	#endif
#endif`,normalmap_pars_fragment:`#ifdef USE_NORMALMAP
	uniform sampler2D normalMap;
	uniform vec2 normalScale;
#endif
#ifdef USE_NORMALMAP_OBJECTSPACE
	uniform mat3 normalMatrix;
#endif
#if ! defined ( USE_TANGENT ) && ( defined ( USE_NORMALMAP_TANGENTSPACE ) || defined ( USE_CLEARCOAT_NORMALMAP ) || defined( USE_ANISOTROPY ) )
	mat3 getTangentFrame( vec3 eye_pos, vec3 surf_norm, vec2 uv ) {
		vec3 q0 = dFdx( eye_pos.xyz );
		vec3 q1 = dFdy( eye_pos.xyz );
		vec2 st0 = dFdx( uv.st );
		vec2 st1 = dFdy( uv.st );
		vec3 N = surf_norm;
		vec3 q1perp = cross( q1, N );
		vec3 q0perp = cross( N, q0 );
		vec3 T = q1perp * st0.x + q0perp * st1.x;
		vec3 B = q1perp * st0.y + q0perp * st1.y;
		float det = max( dot( T, T ), dot( B, B ) );
		float scale = ( det == 0.0 ) ? 0.0 : inversesqrt( det );
		return mat3( T * scale, B * scale, N );
	}
#endif`,clearcoat_normal_fragment_begin:`#ifdef USE_CLEARCOAT
	vec3 clearcoatNormal = nonPerturbedNormal;
#endif`,clearcoat_normal_fragment_maps:`#ifdef USE_CLEARCOAT_NORMALMAP
	vec3 clearcoatMapN = texture2D( clearcoatNormalMap, vClearcoatNormalMapUv ).xyz * 2.0 - 1.0;
	clearcoatMapN.xy *= clearcoatNormalScale;
	clearcoatNormal = normalize( tbn2 * clearcoatMapN );
#endif`,clearcoat_pars_fragment:`#ifdef USE_CLEARCOATMAP
	uniform sampler2D clearcoatMap;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	uniform sampler2D clearcoatNormalMap;
	uniform vec2 clearcoatNormalScale;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	uniform sampler2D clearcoatRoughnessMap;
#endif`,iridescence_pars_fragment:`#ifdef USE_IRIDESCENCEMAP
	uniform sampler2D iridescenceMap;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	uniform sampler2D iridescenceThicknessMap;
#endif`,opaque_fragment:`#ifdef OPAQUE
diffuseColor.a = 1.0;
#endif
#ifdef USE_TRANSMISSION
diffuseColor.a *= material.transmissionAlpha;
#endif
gl_FragColor = vec4( outgoingLight, diffuseColor.a );`,packing:`vec3 packNormalToRGB( const in vec3 normal ) {
	return normalize( normal ) * 0.5 + 0.5;
}
vec3 unpackRGBToNormal( const in vec3 rgb ) {
	return 2.0 * rgb.xyz - 1.0;
}
const float PackUpscale = 256. / 255.;const float UnpackDownscale = 255. / 256.;const float ShiftRight8 = 1. / 256.;
const float Inv255 = 1. / 255.;
const vec4 PackFactors = vec4( 1.0, 256.0, 256.0 * 256.0, 256.0 * 256.0 * 256.0 );
const vec2 UnpackFactors2 = vec2( UnpackDownscale, 1.0 / PackFactors.g );
const vec3 UnpackFactors3 = vec3( UnpackDownscale / PackFactors.rg, 1.0 / PackFactors.b );
const vec4 UnpackFactors4 = vec4( UnpackDownscale / PackFactors.rgb, 1.0 / PackFactors.a );
vec4 packDepthToRGBA( const in float v ) {
	if( v <= 0.0 )
		return vec4( 0., 0., 0., 0. );
	if( v >= 1.0 )
		return vec4( 1., 1., 1., 1. );
	float vuf;
	float af = modf( v * PackFactors.a, vuf );
	float bf = modf( vuf * ShiftRight8, vuf );
	float gf = modf( vuf * ShiftRight8, vuf );
	return vec4( vuf * Inv255, gf * PackUpscale, bf * PackUpscale, af );
}
vec3 packDepthToRGB( const in float v ) {
	if( v <= 0.0 )
		return vec3( 0., 0., 0. );
	if( v >= 1.0 )
		return vec3( 1., 1., 1. );
	float vuf;
	float bf = modf( v * PackFactors.b, vuf );
	float gf = modf( vuf * ShiftRight8, vuf );
	return vec3( vuf * Inv255, gf * PackUpscale, bf );
}
vec2 packDepthToRG( const in float v ) {
	if( v <= 0.0 )
		return vec2( 0., 0. );
	if( v >= 1.0 )
		return vec2( 1., 1. );
	float vuf;
	float gf = modf( v * 256., vuf );
	return vec2( vuf * Inv255, gf );
}
float unpackRGBAToDepth( const in vec4 v ) {
	return dot( v, UnpackFactors4 );
}
float unpackRGBToDepth( const in vec3 v ) {
	return dot( v, UnpackFactors3 );
}
float unpackRGToDepth( const in vec2 v ) {
	return v.r * UnpackFactors2.r + v.g * UnpackFactors2.g;
}
vec4 pack2HalfToRGBA( const in vec2 v ) {
	vec4 r = vec4( v.x, fract( v.x * 255.0 ), v.y, fract( v.y * 255.0 ) );
	return vec4( r.x - r.y / 255.0, r.y, r.z - r.w / 255.0, r.w );
}
vec2 unpackRGBATo2Half( const in vec4 v ) {
	return vec2( v.x + ( v.y / 255.0 ), v.z + ( v.w / 255.0 ) );
}
float viewZToOrthographicDepth( const in float viewZ, const in float near, const in float far ) {
	return ( viewZ + near ) / ( near - far );
}
float orthographicDepthToViewZ( const in float depth, const in float near, const in float far ) {
	#ifdef USE_REVERSED_DEPTH_BUFFER
	
		return depth * ( far - near ) - far;
	#else
		return depth * ( near - far ) - near;
	#endif
}
float viewZToPerspectiveDepth( const in float viewZ, const in float near, const in float far ) {
	return ( ( near + viewZ ) * far ) / ( ( far - near ) * viewZ );
}
float perspectiveDepthToViewZ( const in float depth, const in float near, const in float far ) {
	
	#ifdef USE_REVERSED_DEPTH_BUFFER
		return ( near * far ) / ( ( near - far ) * depth - near );
	#else
		return ( near * far ) / ( ( far - near ) * depth - far );
	#endif
}`,premultiplied_alpha_fragment:`#ifdef PREMULTIPLIED_ALPHA
	gl_FragColor.rgb *= gl_FragColor.a;
#endif`,project_vertex:`vec4 mvPosition = vec4( transformed, 1.0 );
#ifdef USE_BATCHING
	mvPosition = batchingMatrix * mvPosition;
#endif
#ifdef USE_INSTANCING
	mvPosition = instanceMatrix * mvPosition;
#endif
mvPosition = modelViewMatrix * mvPosition;
gl_Position = projectionMatrix * mvPosition;`,dithering_fragment:`#ifdef DITHERING
	gl_FragColor.rgb = dithering( gl_FragColor.rgb );
#endif`,dithering_pars_fragment:`#ifdef DITHERING
	vec3 dithering( vec3 color ) {
		float grid_position = rand( gl_FragCoord.xy );
		vec3 dither_shift_RGB = vec3( 0.25 / 255.0, -0.25 / 255.0, 0.25 / 255.0 );
		dither_shift_RGB = mix( 2.0 * dither_shift_RGB, -2.0 * dither_shift_RGB, grid_position );
		return color + dither_shift_RGB;
	}
#endif`,roughnessmap_fragment:`float roughnessFactor = roughness;
#ifdef USE_ROUGHNESSMAP
	vec4 texelRoughness = texture2D( roughnessMap, vRoughnessMapUv );
	roughnessFactor *= texelRoughness.g;
#endif`,roughnessmap_pars_fragment:`#ifdef USE_ROUGHNESSMAP
	uniform sampler2D roughnessMap;
#endif`,shadowmap_pars_fragment:`#if NUM_SPOT_LIGHT_COORDS > 0
	varying vec4 vSpotLightCoord[ NUM_SPOT_LIGHT_COORDS ];
#endif
#if NUM_SPOT_LIGHT_MAPS > 0
	uniform sampler2D spotLightMap[ NUM_SPOT_LIGHT_MAPS ];
#endif
#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform sampler2DShadow directionalShadowMap[ NUM_DIR_LIGHT_SHADOWS ];
		#else
			uniform sampler2D directionalShadowMap[ NUM_DIR_LIGHT_SHADOWS ];
		#endif
		varying vec4 vDirectionalShadowCoord[ NUM_DIR_LIGHT_SHADOWS ];
		struct DirectionalLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform DirectionalLightShadow directionalLightShadows[ NUM_DIR_LIGHT_SHADOWS ];
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform sampler2DShadow spotShadowMap[ NUM_SPOT_LIGHT_SHADOWS ];
		#else
			uniform sampler2D spotShadowMap[ NUM_SPOT_LIGHT_SHADOWS ];
		#endif
		struct SpotLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform SpotLightShadow spotLightShadows[ NUM_SPOT_LIGHT_SHADOWS ];
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		#if defined( SHADOWMAP_TYPE_PCF )
			uniform samplerCubeShadow pointShadowMap[ NUM_POINT_LIGHT_SHADOWS ];
		#elif defined( SHADOWMAP_TYPE_BASIC )
			uniform samplerCube pointShadowMap[ NUM_POINT_LIGHT_SHADOWS ];
		#endif
		varying vec4 vPointShadowCoord[ NUM_POINT_LIGHT_SHADOWS ];
		struct PointLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
			float shadowCameraNear;
			float shadowCameraFar;
		};
		uniform PointLightShadow pointLightShadows[ NUM_POINT_LIGHT_SHADOWS ];
	#endif
	#if defined( SHADOWMAP_TYPE_PCF )
		float interleavedGradientNoise( vec2 position ) {
			return fract( 52.9829189 * fract( dot( position, vec2( 0.06711056, 0.00583715 ) ) ) );
		}
		vec2 vogelDiskSample( int sampleIndex, int samplesCount, float phi ) {
			const float goldenAngle = 2.399963229728653;
			float r = sqrt( ( float( sampleIndex ) + 0.5 ) / float( samplesCount ) );
			float theta = float( sampleIndex ) * goldenAngle + phi;
			return vec2( cos( theta ), sin( theta ) ) * r;
		}
	#endif
	#if defined( SHADOWMAP_TYPE_PCF )
		float getShadow( sampler2DShadow shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			shadowCoord.z += shadowBias;
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				vec2 texelSize = vec2( 1.0 ) / shadowMapSize;
				float radius = shadowRadius * texelSize.x;
				float phi = interleavedGradientNoise( gl_FragCoord.xy ) * PI2;
				shadow = (
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 0, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 1, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 2, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 3, 5, phi ) * radius, shadowCoord.z ) ) +
					texture( shadowMap, vec3( shadowCoord.xy + vogelDiskSample( 4, 5, phi ) * radius, shadowCoord.z ) )
				) * 0.2;
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#elif defined( SHADOWMAP_TYPE_VSM )
		float getShadow( sampler2D shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				shadowCoord.z -= shadowBias;
			#else
				shadowCoord.z += shadowBias;
			#endif
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				vec2 distribution = texture2D( shadowMap, shadowCoord.xy ).rg;
				float mean = distribution.x;
				float variance = distribution.y * distribution.y;
				#ifdef USE_REVERSED_DEPTH_BUFFER
					float hard_shadow = step( mean, shadowCoord.z );
				#else
					float hard_shadow = step( shadowCoord.z, mean );
				#endif
				
				if ( hard_shadow == 1.0 ) {
					shadow = 1.0;
				} else {
					variance = max( variance, 0.0000001 );
					float d = shadowCoord.z - mean;
					float p_max = variance / ( variance + d * d );
					p_max = clamp( ( p_max - 0.3 ) / 0.65, 0.0, 1.0 );
					shadow = max( hard_shadow, p_max );
				}
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#else
		float getShadow( sampler2D shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord ) {
			float shadow = 1.0;
			shadowCoord.xyz /= shadowCoord.w;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				shadowCoord.z -= shadowBias;
			#else
				shadowCoord.z += shadowBias;
			#endif
			bool inFrustum = shadowCoord.x >= 0.0 && shadowCoord.x <= 1.0 && shadowCoord.y >= 0.0 && shadowCoord.y <= 1.0;
			bool frustumTest = inFrustum && shadowCoord.z <= 1.0;
			if ( frustumTest ) {
				float depth = texture2D( shadowMap, shadowCoord.xy ).r;
				#ifdef USE_REVERSED_DEPTH_BUFFER
					shadow = step( depth, shadowCoord.z );
				#else
					shadow = step( shadowCoord.z, depth );
				#endif
			}
			return mix( 1.0, shadow, shadowIntensity );
		}
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
	#if defined( SHADOWMAP_TYPE_PCF )
	float getPointShadow( samplerCubeShadow shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord, float shadowCameraNear, float shadowCameraFar ) {
		float shadow = 1.0;
		vec3 lightToPosition = shadowCoord.xyz;
		vec3 bd3D = normalize( lightToPosition );
		vec3 absVec = abs( lightToPosition );
		float viewSpaceZ = max( max( absVec.x, absVec.y ), absVec.z );
		if ( viewSpaceZ - shadowCameraFar <= 0.0 && viewSpaceZ - shadowCameraNear >= 0.0 ) {
			#ifdef USE_REVERSED_DEPTH_BUFFER
				float dp = ( shadowCameraNear * ( shadowCameraFar - viewSpaceZ ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
				dp -= shadowBias;
			#else
				float dp = ( shadowCameraFar * ( viewSpaceZ - shadowCameraNear ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
				dp += shadowBias;
			#endif
			float texelSize = shadowRadius / shadowMapSize.x;
			vec3 absDir = abs( bd3D );
			vec3 tangent = absDir.x > absDir.z ? vec3( 0.0, 1.0, 0.0 ) : vec3( 1.0, 0.0, 0.0 );
			tangent = normalize( cross( bd3D, tangent ) );
			vec3 bitangent = cross( bd3D, tangent );
			float phi = interleavedGradientNoise( gl_FragCoord.xy ) * PI2;
			vec2 sample0 = vogelDiskSample( 0, 5, phi );
			vec2 sample1 = vogelDiskSample( 1, 5, phi );
			vec2 sample2 = vogelDiskSample( 2, 5, phi );
			vec2 sample3 = vogelDiskSample( 3, 5, phi );
			vec2 sample4 = vogelDiskSample( 4, 5, phi );
			shadow = (
				texture( shadowMap, vec4( bd3D + ( tangent * sample0.x + bitangent * sample0.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample1.x + bitangent * sample1.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample2.x + bitangent * sample2.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample3.x + bitangent * sample3.y ) * texelSize, dp ) ) +
				texture( shadowMap, vec4( bd3D + ( tangent * sample4.x + bitangent * sample4.y ) * texelSize, dp ) )
			) * 0.2;
		}
		return mix( 1.0, shadow, shadowIntensity );
	}
	#elif defined( SHADOWMAP_TYPE_BASIC )
	float getPointShadow( samplerCube shadowMap, vec2 shadowMapSize, float shadowIntensity, float shadowBias, float shadowRadius, vec4 shadowCoord, float shadowCameraNear, float shadowCameraFar ) {
		float shadow = 1.0;
		vec3 lightToPosition = shadowCoord.xyz;
		vec3 absVec = abs( lightToPosition );
		float viewSpaceZ = max( max( absVec.x, absVec.y ), absVec.z );
		if ( viewSpaceZ - shadowCameraFar <= 0.0 && viewSpaceZ - shadowCameraNear >= 0.0 ) {
			float dp = ( shadowCameraFar * ( viewSpaceZ - shadowCameraNear ) ) / ( viewSpaceZ * ( shadowCameraFar - shadowCameraNear ) );
			dp += shadowBias;
			vec3 bd3D = normalize( lightToPosition );
			float depth = textureCube( shadowMap, bd3D ).r;
			#ifdef USE_REVERSED_DEPTH_BUFFER
				depth = 1.0 - depth;
			#endif
			shadow = step( dp, depth );
		}
		return mix( 1.0, shadow, shadowIntensity );
	}
	#endif
	#endif
#endif`,shadowmap_pars_vertex:`#if NUM_SPOT_LIGHT_COORDS > 0
	uniform mat4 spotLightMatrix[ NUM_SPOT_LIGHT_COORDS ];
	varying vec4 vSpotLightCoord[ NUM_SPOT_LIGHT_COORDS ];
#endif
#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
		uniform mat4 directionalShadowMatrix[ NUM_DIR_LIGHT_SHADOWS ];
		varying vec4 vDirectionalShadowCoord[ NUM_DIR_LIGHT_SHADOWS ];
		struct DirectionalLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform DirectionalLightShadow directionalLightShadows[ NUM_DIR_LIGHT_SHADOWS ];
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
		struct SpotLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
		};
		uniform SpotLightShadow spotLightShadows[ NUM_SPOT_LIGHT_SHADOWS ];
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		uniform mat4 pointShadowMatrix[ NUM_POINT_LIGHT_SHADOWS ];
		varying vec4 vPointShadowCoord[ NUM_POINT_LIGHT_SHADOWS ];
		struct PointLightShadow {
			float shadowIntensity;
			float shadowBias;
			float shadowNormalBias;
			float shadowRadius;
			vec2 shadowMapSize;
			float shadowCameraNear;
			float shadowCameraFar;
		};
		uniform PointLightShadow pointLightShadows[ NUM_POINT_LIGHT_SHADOWS ];
	#endif
#endif`,shadowmap_vertex:`#if ( defined( USE_SHADOWMAP ) && ( NUM_DIR_LIGHT_SHADOWS > 0 || NUM_POINT_LIGHT_SHADOWS > 0 ) ) || ( NUM_SPOT_LIGHT_COORDS > 0 )
	vec3 shadowWorldNormal = inverseTransformDirection( transformedNormal, viewMatrix );
	vec4 shadowWorldPosition;
#endif
#if defined( USE_SHADOWMAP )
	#if NUM_DIR_LIGHT_SHADOWS > 0
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i ++ ) {
			shadowWorldPosition = worldPosition + vec4( shadowWorldNormal * directionalLightShadows[ i ].shadowNormalBias, 0 );
			vDirectionalShadowCoord[ i ] = directionalShadowMatrix[ i ] * shadowWorldPosition;
		}
		#pragma unroll_loop_end
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0
		#pragma unroll_loop_start
		for ( int i = 0; i < NUM_POINT_LIGHT_SHADOWS; i ++ ) {
			shadowWorldPosition = worldPosition + vec4( shadowWorldNormal * pointLightShadows[ i ].shadowNormalBias, 0 );
			vPointShadowCoord[ i ] = pointShadowMatrix[ i ] * shadowWorldPosition;
		}
		#pragma unroll_loop_end
	#endif
#endif
#if NUM_SPOT_LIGHT_COORDS > 0
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHT_COORDS; i ++ ) {
		shadowWorldPosition = worldPosition;
		#if ( defined( USE_SHADOWMAP ) && UNROLLED_LOOP_INDEX < NUM_SPOT_LIGHT_SHADOWS )
			shadowWorldPosition.xyz += shadowWorldNormal * spotLightShadows[ i ].shadowNormalBias;
		#endif
		vSpotLightCoord[ i ] = spotLightMatrix[ i ] * shadowWorldPosition;
	}
	#pragma unroll_loop_end
#endif`,shadowmask_pars_fragment:`float getShadowMask() {
	float shadow = 1.0;
	#ifdef USE_SHADOWMAP
	#if NUM_DIR_LIGHT_SHADOWS > 0
	DirectionalLightShadow directionalLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i ++ ) {
		directionalLight = directionalLightShadows[ i ];
		shadow *= receiveShadow ? getShadow( directionalShadowMap[ i ], directionalLight.shadowMapSize, directionalLight.shadowIntensity, directionalLight.shadowBias, directionalLight.shadowRadius, vDirectionalShadowCoord[ i ] ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#if NUM_SPOT_LIGHT_SHADOWS > 0
	SpotLightShadow spotLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_SPOT_LIGHT_SHADOWS; i ++ ) {
		spotLight = spotLightShadows[ i ];
		shadow *= receiveShadow ? getShadow( spotShadowMap[ i ], spotLight.shadowMapSize, spotLight.shadowIntensity, spotLight.shadowBias, spotLight.shadowRadius, vSpotLightCoord[ i ] ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#if NUM_POINT_LIGHT_SHADOWS > 0 && ( defined( SHADOWMAP_TYPE_PCF ) || defined( SHADOWMAP_TYPE_BASIC ) )
	PointLightShadow pointLight;
	#pragma unroll_loop_start
	for ( int i = 0; i < NUM_POINT_LIGHT_SHADOWS; i ++ ) {
		pointLight = pointLightShadows[ i ];
		shadow *= receiveShadow ? getPointShadow( pointShadowMap[ i ], pointLight.shadowMapSize, pointLight.shadowIntensity, pointLight.shadowBias, pointLight.shadowRadius, vPointShadowCoord[ i ], pointLight.shadowCameraNear, pointLight.shadowCameraFar ) : 1.0;
	}
	#pragma unroll_loop_end
	#endif
	#endif
	return shadow;
}`,skinbase_vertex:`#ifdef USE_SKINNING
	mat4 boneMatX = getBoneMatrix( skinIndex.x );
	mat4 boneMatY = getBoneMatrix( skinIndex.y );
	mat4 boneMatZ = getBoneMatrix( skinIndex.z );
	mat4 boneMatW = getBoneMatrix( skinIndex.w );
#endif`,skinning_pars_vertex:`#ifdef USE_SKINNING
	uniform mat4 bindMatrix;
	uniform mat4 bindMatrixInverse;
	uniform highp sampler2D boneTexture;
	mat4 getBoneMatrix( const in float i ) {
		int size = textureSize( boneTexture, 0 ).x;
		int j = int( i ) * 4;
		int x = j % size;
		int y = j / size;
		vec4 v1 = texelFetch( boneTexture, ivec2( x, y ), 0 );
		vec4 v2 = texelFetch( boneTexture, ivec2( x + 1, y ), 0 );
		vec4 v3 = texelFetch( boneTexture, ivec2( x + 2, y ), 0 );
		vec4 v4 = texelFetch( boneTexture, ivec2( x + 3, y ), 0 );
		return mat4( v1, v2, v3, v4 );
	}
#endif`,skinning_vertex:`#ifdef USE_SKINNING
	vec4 skinVertex = bindMatrix * vec4( transformed, 1.0 );
	vec4 skinned = vec4( 0.0 );
	skinned += boneMatX * skinVertex * skinWeight.x;
	skinned += boneMatY * skinVertex * skinWeight.y;
	skinned += boneMatZ * skinVertex * skinWeight.z;
	skinned += boneMatW * skinVertex * skinWeight.w;
	transformed = ( bindMatrixInverse * skinned ).xyz;
#endif`,skinnormal_vertex:`#ifdef USE_SKINNING
	mat4 skinMatrix = mat4( 0.0 );
	skinMatrix += skinWeight.x * boneMatX;
	skinMatrix += skinWeight.y * boneMatY;
	skinMatrix += skinWeight.z * boneMatZ;
	skinMatrix += skinWeight.w * boneMatW;
	skinMatrix = bindMatrixInverse * skinMatrix * bindMatrix;
	objectNormal = vec4( skinMatrix * vec4( objectNormal, 0.0 ) ).xyz;
	#ifdef USE_TANGENT
		objectTangent = vec4( skinMatrix * vec4( objectTangent, 0.0 ) ).xyz;
	#endif
#endif`,specularmap_fragment:`float specularStrength;
#ifdef USE_SPECULARMAP
	vec4 texelSpecular = texture2D( specularMap, vSpecularMapUv );
	specularStrength = texelSpecular.r;
#else
	specularStrength = 1.0;
#endif`,specularmap_pars_fragment:`#ifdef USE_SPECULARMAP
	uniform sampler2D specularMap;
#endif`,tonemapping_fragment:`#if defined( TONE_MAPPING )
	gl_FragColor.rgb = toneMapping( gl_FragColor.rgb );
#endif`,tonemapping_pars_fragment:`#ifndef saturate
#define saturate( a ) clamp( a, 0.0, 1.0 )
#endif
uniform float toneMappingExposure;
vec3 LinearToneMapping( vec3 color ) {
	return saturate( toneMappingExposure * color );
}
vec3 ReinhardToneMapping( vec3 color ) {
	color *= toneMappingExposure;
	return saturate( color / ( vec3( 1.0 ) + color ) );
}
vec3 CineonToneMapping( vec3 color ) {
	color *= toneMappingExposure;
	color = max( vec3( 0.0 ), color - 0.004 );
	return pow( ( color * ( 6.2 * color + 0.5 ) ) / ( color * ( 6.2 * color + 1.7 ) + 0.06 ), vec3( 2.2 ) );
}
vec3 RRTAndODTFit( vec3 v ) {
	vec3 a = v * ( v + 0.0245786 ) - 0.000090537;
	vec3 b = v * ( 0.983729 * v + 0.4329510 ) + 0.238081;
	return a / b;
}
vec3 ACESFilmicToneMapping( vec3 color ) {
	const mat3 ACESInputMat = mat3(
		vec3( 0.59719, 0.07600, 0.02840 ),		vec3( 0.35458, 0.90834, 0.13383 ),
		vec3( 0.04823, 0.01566, 0.83777 )
	);
	const mat3 ACESOutputMat = mat3(
		vec3(  1.60475, -0.10208, -0.00327 ),		vec3( -0.53108,  1.10813, -0.07276 ),
		vec3( -0.07367, -0.00605,  1.07602 )
	);
	color *= toneMappingExposure / 0.6;
	color = ACESInputMat * color;
	color = RRTAndODTFit( color );
	color = ACESOutputMat * color;
	return saturate( color );
}
const mat3 LINEAR_REC2020_TO_LINEAR_SRGB = mat3(
	vec3( 1.6605, - 0.1246, - 0.0182 ),
	vec3( - 0.5876, 1.1329, - 0.1006 ),
	vec3( - 0.0728, - 0.0083, 1.1187 )
);
const mat3 LINEAR_SRGB_TO_LINEAR_REC2020 = mat3(
	vec3( 0.6274, 0.0691, 0.0164 ),
	vec3( 0.3293, 0.9195, 0.0880 ),
	vec3( 0.0433, 0.0113, 0.8956 )
);
vec3 agxDefaultContrastApprox( vec3 x ) {
	vec3 x2 = x * x;
	vec3 x4 = x2 * x2;
	return + 15.5 * x4 * x2
		- 40.14 * x4 * x
		+ 31.96 * x4
		- 6.868 * x2 * x
		+ 0.4298 * x2
		+ 0.1191 * x
		- 0.00232;
}
vec3 AgXToneMapping( vec3 color ) {
	const mat3 AgXInsetMatrix = mat3(
		vec3( 0.856627153315983, 0.137318972929847, 0.11189821299995 ),
		vec3( 0.0951212405381588, 0.761241990602591, 0.0767994186031903 ),
		vec3( 0.0482516061458583, 0.101439036467562, 0.811302368396859 )
	);
	const mat3 AgXOutsetMatrix = mat3(
		vec3( 1.1271005818144368, - 0.1413297634984383, - 0.14132976349843826 ),
		vec3( - 0.11060664309660323, 1.157823702216272, - 0.11060664309660294 ),
		vec3( - 0.016493938717834573, - 0.016493938717834257, 1.2519364065950405 )
	);
	const float AgxMinEv = - 12.47393;	const float AgxMaxEv = 4.026069;
	color *= toneMappingExposure;
	color = LINEAR_SRGB_TO_LINEAR_REC2020 * color;
	color = AgXInsetMatrix * color;
	color = max( color, 1e-10 );	color = log2( color );
	color = ( color - AgxMinEv ) / ( AgxMaxEv - AgxMinEv );
	color = clamp( color, 0.0, 1.0 );
	color = agxDefaultContrastApprox( color );
	color = AgXOutsetMatrix * color;
	color = pow( max( vec3( 0.0 ), color ), vec3( 2.2 ) );
	color = LINEAR_REC2020_TO_LINEAR_SRGB * color;
	color = clamp( color, 0.0, 1.0 );
	return color;
}
vec3 NeutralToneMapping( vec3 color ) {
	const float StartCompression = 0.8 - 0.04;
	const float Desaturation = 0.15;
	color *= toneMappingExposure;
	float x = min( color.r, min( color.g, color.b ) );
	float offset = x < 0.08 ? x - 6.25 * x * x : 0.04;
	color -= offset;
	float peak = max( color.r, max( color.g, color.b ) );
	if ( peak < StartCompression ) return color;
	float d = 1. - StartCompression;
	float newPeak = 1. - d * d / ( peak + d - StartCompression );
	color *= newPeak / peak;
	float g = 1. - 1. / ( Desaturation * ( peak - newPeak ) + 1. );
	return mix( color, vec3( newPeak ), g );
}
vec3 CustomToneMapping( vec3 color ) { return color; }`,transmission_fragment:`#ifdef USE_TRANSMISSION
	material.transmission = transmission;
	material.transmissionAlpha = 1.0;
	material.thickness = thickness;
	material.attenuationDistance = attenuationDistance;
	material.attenuationColor = attenuationColor;
	#ifdef USE_TRANSMISSIONMAP
		material.transmission *= texture2D( transmissionMap, vTransmissionMapUv ).r;
	#endif
	#ifdef USE_THICKNESSMAP
		material.thickness *= texture2D( thicknessMap, vThicknessMapUv ).g;
	#endif
	vec3 pos = vWorldPosition;
	vec3 v = normalize( cameraPosition - pos );
	vec3 n = inverseTransformDirection( normal, viewMatrix );
	vec4 transmitted = getIBLVolumeRefraction(
		n, v, material.roughness, material.diffuseContribution, material.specularColorBlended, material.specularF90,
		pos, modelMatrix, viewMatrix, projectionMatrix, material.dispersion, material.ior, material.thickness,
		material.attenuationColor, material.attenuationDistance );
	material.transmissionAlpha = mix( material.transmissionAlpha, transmitted.a, material.transmission );
	totalDiffuse = mix( totalDiffuse, transmitted.rgb, material.transmission );
#endif`,transmission_pars_fragment:`#ifdef USE_TRANSMISSION
	uniform float transmission;
	uniform float thickness;
	uniform float attenuationDistance;
	uniform vec3 attenuationColor;
	#ifdef USE_TRANSMISSIONMAP
		uniform sampler2D transmissionMap;
	#endif
	#ifdef USE_THICKNESSMAP
		uniform sampler2D thicknessMap;
	#endif
	uniform vec2 transmissionSamplerSize;
	uniform sampler2D transmissionSamplerMap;
	uniform mat4 modelMatrix;
	uniform mat4 projectionMatrix;
	varying vec3 vWorldPosition;
	float w0( float a ) {
		return ( 1.0 / 6.0 ) * ( a * ( a * ( - a + 3.0 ) - 3.0 ) + 1.0 );
	}
	float w1( float a ) {
		return ( 1.0 / 6.0 ) * ( a *  a * ( 3.0 * a - 6.0 ) + 4.0 );
	}
	float w2( float a ){
		return ( 1.0 / 6.0 ) * ( a * ( a * ( - 3.0 * a + 3.0 ) + 3.0 ) + 1.0 );
	}
	float w3( float a ) {
		return ( 1.0 / 6.0 ) * ( a * a * a );
	}
	float g0( float a ) {
		return w0( a ) + w1( a );
	}
	float g1( float a ) {
		return w2( a ) + w3( a );
	}
	float h0( float a ) {
		return - 1.0 + w1( a ) / ( w0( a ) + w1( a ) );
	}
	float h1( float a ) {
		return 1.0 + w3( a ) / ( w2( a ) + w3( a ) );
	}
	vec4 bicubic( sampler2D tex, vec2 uv, vec4 texelSize, float lod ) {
		uv = uv * texelSize.zw + 0.5;
		vec2 iuv = floor( uv );
		vec2 fuv = fract( uv );
		float g0x = g0( fuv.x );
		float g1x = g1( fuv.x );
		float h0x = h0( fuv.x );
		float h1x = h1( fuv.x );
		float h0y = h0( fuv.y );
		float h1y = h1( fuv.y );
		vec2 p0 = ( vec2( iuv.x + h0x, iuv.y + h0y ) - 0.5 ) * texelSize.xy;
		vec2 p1 = ( vec2( iuv.x + h1x, iuv.y + h0y ) - 0.5 ) * texelSize.xy;
		vec2 p2 = ( vec2( iuv.x + h0x, iuv.y + h1y ) - 0.5 ) * texelSize.xy;
		vec2 p3 = ( vec2( iuv.x + h1x, iuv.y + h1y ) - 0.5 ) * texelSize.xy;
		return g0( fuv.y ) * ( g0x * textureLod( tex, p0, lod ) + g1x * textureLod( tex, p1, lod ) ) +
			g1( fuv.y ) * ( g0x * textureLod( tex, p2, lod ) + g1x * textureLod( tex, p3, lod ) );
	}
	vec4 textureBicubic( sampler2D sampler, vec2 uv, float lod ) {
		vec2 fLodSize = vec2( textureSize( sampler, int( lod ) ) );
		vec2 cLodSize = vec2( textureSize( sampler, int( lod + 1.0 ) ) );
		vec2 fLodSizeInv = 1.0 / fLodSize;
		vec2 cLodSizeInv = 1.0 / cLodSize;
		vec4 fSample = bicubic( sampler, uv, vec4( fLodSizeInv, fLodSize ), floor( lod ) );
		vec4 cSample = bicubic( sampler, uv, vec4( cLodSizeInv, cLodSize ), ceil( lod ) );
		return mix( fSample, cSample, fract( lod ) );
	}
	vec3 getVolumeTransmissionRay( const in vec3 n, const in vec3 v, const in float thickness, const in float ior, const in mat4 modelMatrix ) {
		vec3 refractionVector = refract( - v, normalize( n ), 1.0 / ior );
		vec3 modelScale;
		modelScale.x = length( vec3( modelMatrix[ 0 ].xyz ) );
		modelScale.y = length( vec3( modelMatrix[ 1 ].xyz ) );
		modelScale.z = length( vec3( modelMatrix[ 2 ].xyz ) );
		return normalize( refractionVector ) * thickness * modelScale;
	}
	float applyIorToRoughness( const in float roughness, const in float ior ) {
		return roughness * clamp( ior * 2.0 - 2.0, 0.0, 1.0 );
	}
	vec4 getTransmissionSample( const in vec2 fragCoord, const in float roughness, const in float ior ) {
		float lod = log2( transmissionSamplerSize.x ) * applyIorToRoughness( roughness, ior );
		return textureBicubic( transmissionSamplerMap, fragCoord.xy, lod );
	}
	vec3 volumeAttenuation( const in float transmissionDistance, const in vec3 attenuationColor, const in float attenuationDistance ) {
		if ( isinf( attenuationDistance ) ) {
			return vec3( 1.0 );
		} else {
			vec3 attenuationCoefficient = -log( attenuationColor ) / attenuationDistance;
			vec3 transmittance = exp( - attenuationCoefficient * transmissionDistance );			return transmittance;
		}
	}
	vec4 getIBLVolumeRefraction( const in vec3 n, const in vec3 v, const in float roughness, const in vec3 diffuseColor,
		const in vec3 specularColor, const in float specularF90, const in vec3 position, const in mat4 modelMatrix,
		const in mat4 viewMatrix, const in mat4 projMatrix, const in float dispersion, const in float ior, const in float thickness,
		const in vec3 attenuationColor, const in float attenuationDistance ) {
		vec4 transmittedLight;
		vec3 transmittance;
		#ifdef USE_DISPERSION
			float halfSpread = ( ior - 1.0 ) * 0.025 * dispersion;
			vec3 iors = vec3( ior - halfSpread, ior, ior + halfSpread );
			for ( int i = 0; i < 3; i ++ ) {
				vec3 transmissionRay = getVolumeTransmissionRay( n, v, thickness, iors[ i ], modelMatrix );
				vec3 refractedRayExit = position + transmissionRay;
				vec4 ndcPos = projMatrix * viewMatrix * vec4( refractedRayExit, 1.0 );
				vec2 refractionCoords = ndcPos.xy / ndcPos.w;
				refractionCoords += 1.0;
				refractionCoords /= 2.0;
				vec4 transmissionSample = getTransmissionSample( refractionCoords, roughness, iors[ i ] );
				transmittedLight[ i ] = transmissionSample[ i ];
				transmittedLight.a += transmissionSample.a;
				transmittance[ i ] = diffuseColor[ i ] * volumeAttenuation( length( transmissionRay ), attenuationColor, attenuationDistance )[ i ];
			}
			transmittedLight.a /= 3.0;
		#else
			vec3 transmissionRay = getVolumeTransmissionRay( n, v, thickness, ior, modelMatrix );
			vec3 refractedRayExit = position + transmissionRay;
			vec4 ndcPos = projMatrix * viewMatrix * vec4( refractedRayExit, 1.0 );
			vec2 refractionCoords = ndcPos.xy / ndcPos.w;
			refractionCoords += 1.0;
			refractionCoords /= 2.0;
			transmittedLight = getTransmissionSample( refractionCoords, roughness, ior );
			transmittance = diffuseColor * volumeAttenuation( length( transmissionRay ), attenuationColor, attenuationDistance );
		#endif
		vec3 attenuatedColor = transmittance * transmittedLight.rgb;
		vec3 F = EnvironmentBRDF( n, v, specularColor, specularF90, roughness );
		float transmittanceFactor = ( transmittance.r + transmittance.g + transmittance.b ) / 3.0;
		return vec4( ( 1.0 - F ) * attenuatedColor, 1.0 - ( 1.0 - transmittedLight.a ) * transmittanceFactor );
	}
#endif`,uv_pars_fragment:`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	varying vec2 vUv;
#endif
#ifdef USE_MAP
	varying vec2 vMapUv;
#endif
#ifdef USE_ALPHAMAP
	varying vec2 vAlphaMapUv;
#endif
#ifdef USE_LIGHTMAP
	varying vec2 vLightMapUv;
#endif
#ifdef USE_AOMAP
	varying vec2 vAoMapUv;
#endif
#ifdef USE_BUMPMAP
	varying vec2 vBumpMapUv;
#endif
#ifdef USE_NORMALMAP
	varying vec2 vNormalMapUv;
#endif
#ifdef USE_EMISSIVEMAP
	varying vec2 vEmissiveMapUv;
#endif
#ifdef USE_METALNESSMAP
	varying vec2 vMetalnessMapUv;
#endif
#ifdef USE_ROUGHNESSMAP
	varying vec2 vRoughnessMapUv;
#endif
#ifdef USE_ANISOTROPYMAP
	varying vec2 vAnisotropyMapUv;
#endif
#ifdef USE_CLEARCOATMAP
	varying vec2 vClearcoatMapUv;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	varying vec2 vClearcoatNormalMapUv;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	varying vec2 vClearcoatRoughnessMapUv;
#endif
#ifdef USE_IRIDESCENCEMAP
	varying vec2 vIridescenceMapUv;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	varying vec2 vIridescenceThicknessMapUv;
#endif
#ifdef USE_SHEEN_COLORMAP
	varying vec2 vSheenColorMapUv;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	varying vec2 vSheenRoughnessMapUv;
#endif
#ifdef USE_SPECULARMAP
	varying vec2 vSpecularMapUv;
#endif
#ifdef USE_SPECULAR_COLORMAP
	varying vec2 vSpecularColorMapUv;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	varying vec2 vSpecularIntensityMapUv;
#endif
#ifdef USE_TRANSMISSIONMAP
	uniform mat3 transmissionMapTransform;
	varying vec2 vTransmissionMapUv;
#endif
#ifdef USE_THICKNESSMAP
	uniform mat3 thicknessMapTransform;
	varying vec2 vThicknessMapUv;
#endif`,uv_pars_vertex:`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	varying vec2 vUv;
#endif
#ifdef USE_MAP
	uniform mat3 mapTransform;
	varying vec2 vMapUv;
#endif
#ifdef USE_ALPHAMAP
	uniform mat3 alphaMapTransform;
	varying vec2 vAlphaMapUv;
#endif
#ifdef USE_LIGHTMAP
	uniform mat3 lightMapTransform;
	varying vec2 vLightMapUv;
#endif
#ifdef USE_AOMAP
	uniform mat3 aoMapTransform;
	varying vec2 vAoMapUv;
#endif
#ifdef USE_BUMPMAP
	uniform mat3 bumpMapTransform;
	varying vec2 vBumpMapUv;
#endif
#ifdef USE_NORMALMAP
	uniform mat3 normalMapTransform;
	varying vec2 vNormalMapUv;
#endif
#ifdef USE_DISPLACEMENTMAP
	uniform mat3 displacementMapTransform;
	varying vec2 vDisplacementMapUv;
#endif
#ifdef USE_EMISSIVEMAP
	uniform mat3 emissiveMapTransform;
	varying vec2 vEmissiveMapUv;
#endif
#ifdef USE_METALNESSMAP
	uniform mat3 metalnessMapTransform;
	varying vec2 vMetalnessMapUv;
#endif
#ifdef USE_ROUGHNESSMAP
	uniform mat3 roughnessMapTransform;
	varying vec2 vRoughnessMapUv;
#endif
#ifdef USE_ANISOTROPYMAP
	uniform mat3 anisotropyMapTransform;
	varying vec2 vAnisotropyMapUv;
#endif
#ifdef USE_CLEARCOATMAP
	uniform mat3 clearcoatMapTransform;
	varying vec2 vClearcoatMapUv;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	uniform mat3 clearcoatNormalMapTransform;
	varying vec2 vClearcoatNormalMapUv;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	uniform mat3 clearcoatRoughnessMapTransform;
	varying vec2 vClearcoatRoughnessMapUv;
#endif
#ifdef USE_SHEEN_COLORMAP
	uniform mat3 sheenColorMapTransform;
	varying vec2 vSheenColorMapUv;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	uniform mat3 sheenRoughnessMapTransform;
	varying vec2 vSheenRoughnessMapUv;
#endif
#ifdef USE_IRIDESCENCEMAP
	uniform mat3 iridescenceMapTransform;
	varying vec2 vIridescenceMapUv;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	uniform mat3 iridescenceThicknessMapTransform;
	varying vec2 vIridescenceThicknessMapUv;
#endif
#ifdef USE_SPECULARMAP
	uniform mat3 specularMapTransform;
	varying vec2 vSpecularMapUv;
#endif
#ifdef USE_SPECULAR_COLORMAP
	uniform mat3 specularColorMapTransform;
	varying vec2 vSpecularColorMapUv;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	uniform mat3 specularIntensityMapTransform;
	varying vec2 vSpecularIntensityMapUv;
#endif
#ifdef USE_TRANSMISSIONMAP
	uniform mat3 transmissionMapTransform;
	varying vec2 vTransmissionMapUv;
#endif
#ifdef USE_THICKNESSMAP
	uniform mat3 thicknessMapTransform;
	varying vec2 vThicknessMapUv;
#endif`,uv_vertex:`#if defined( USE_UV ) || defined( USE_ANISOTROPY )
	vUv = vec3( uv, 1 ).xy;
#endif
#ifdef USE_MAP
	vMapUv = ( mapTransform * vec3( MAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ALPHAMAP
	vAlphaMapUv = ( alphaMapTransform * vec3( ALPHAMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_LIGHTMAP
	vLightMapUv = ( lightMapTransform * vec3( LIGHTMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_AOMAP
	vAoMapUv = ( aoMapTransform * vec3( AOMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_BUMPMAP
	vBumpMapUv = ( bumpMapTransform * vec3( BUMPMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_NORMALMAP
	vNormalMapUv = ( normalMapTransform * vec3( NORMALMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_DISPLACEMENTMAP
	vDisplacementMapUv = ( displacementMapTransform * vec3( DISPLACEMENTMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_EMISSIVEMAP
	vEmissiveMapUv = ( emissiveMapTransform * vec3( EMISSIVEMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_METALNESSMAP
	vMetalnessMapUv = ( metalnessMapTransform * vec3( METALNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ROUGHNESSMAP
	vRoughnessMapUv = ( roughnessMapTransform * vec3( ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_ANISOTROPYMAP
	vAnisotropyMapUv = ( anisotropyMapTransform * vec3( ANISOTROPYMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOATMAP
	vClearcoatMapUv = ( clearcoatMapTransform * vec3( CLEARCOATMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOAT_NORMALMAP
	vClearcoatNormalMapUv = ( clearcoatNormalMapTransform * vec3( CLEARCOAT_NORMALMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_CLEARCOAT_ROUGHNESSMAP
	vClearcoatRoughnessMapUv = ( clearcoatRoughnessMapTransform * vec3( CLEARCOAT_ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_IRIDESCENCEMAP
	vIridescenceMapUv = ( iridescenceMapTransform * vec3( IRIDESCENCEMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_IRIDESCENCE_THICKNESSMAP
	vIridescenceThicknessMapUv = ( iridescenceThicknessMapTransform * vec3( IRIDESCENCE_THICKNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SHEEN_COLORMAP
	vSheenColorMapUv = ( sheenColorMapTransform * vec3( SHEEN_COLORMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SHEEN_ROUGHNESSMAP
	vSheenRoughnessMapUv = ( sheenRoughnessMapTransform * vec3( SHEEN_ROUGHNESSMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULARMAP
	vSpecularMapUv = ( specularMapTransform * vec3( SPECULARMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULAR_COLORMAP
	vSpecularColorMapUv = ( specularColorMapTransform * vec3( SPECULAR_COLORMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_SPECULAR_INTENSITYMAP
	vSpecularIntensityMapUv = ( specularIntensityMapTransform * vec3( SPECULAR_INTENSITYMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_TRANSMISSIONMAP
	vTransmissionMapUv = ( transmissionMapTransform * vec3( TRANSMISSIONMAP_UV, 1 ) ).xy;
#endif
#ifdef USE_THICKNESSMAP
	vThicknessMapUv = ( thicknessMapTransform * vec3( THICKNESSMAP_UV, 1 ) ).xy;
#endif`,worldpos_vertex:`#if defined( USE_ENVMAP ) || defined( DISTANCE ) || defined ( USE_SHADOWMAP ) || defined ( USE_TRANSMISSION ) || NUM_SPOT_LIGHT_COORDS > 0
	vec4 worldPosition = vec4( transformed, 1.0 );
	#ifdef USE_BATCHING
		worldPosition = batchingMatrix * worldPosition;
	#endif
	#ifdef USE_INSTANCING
		worldPosition = instanceMatrix * worldPosition;
	#endif
	worldPosition = modelMatrix * worldPosition;
#endif`,background_vert:`varying vec2 vUv;
uniform mat3 uvTransform;
void main() {
	vUv = ( uvTransform * vec3( uv, 1 ) ).xy;
	gl_Position = vec4( position.xy, 1.0, 1.0 );
}`,background_frag:`uniform sampler2D t2D;
uniform float backgroundIntensity;
varying vec2 vUv;
void main() {
	vec4 texColor = texture2D( t2D, vUv );
	#ifdef DECODE_VIDEO_TEXTURE
		texColor = vec4( mix( pow( texColor.rgb * 0.9478672986 + vec3( 0.0521327014 ), vec3( 2.4 ) ), texColor.rgb * 0.0773993808, vec3( lessThanEqual( texColor.rgb, vec3( 0.04045 ) ) ) ), texColor.w );
	#endif
	texColor.rgb *= backgroundIntensity;
	gl_FragColor = texColor;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,backgroundCube_vert:`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
	gl_Position.z = gl_Position.w;
}`,backgroundCube_frag:`#ifdef ENVMAP_TYPE_CUBE
	uniform samplerCube envMap;
#elif defined( ENVMAP_TYPE_CUBE_UV )
	uniform sampler2D envMap;
#endif
uniform float flipEnvMap;
uniform float backgroundBlurriness;
uniform float backgroundIntensity;
uniform mat3 backgroundRotation;
varying vec3 vWorldDirection;
#include <cube_uv_reflection_fragment>
void main() {
	#ifdef ENVMAP_TYPE_CUBE
		vec4 texColor = textureCube( envMap, backgroundRotation * vec3( flipEnvMap * vWorldDirection.x, vWorldDirection.yz ) );
	#elif defined( ENVMAP_TYPE_CUBE_UV )
		vec4 texColor = textureCubeUV( envMap, backgroundRotation * vWorldDirection, backgroundBlurriness );
	#else
		vec4 texColor = vec4( 0.0, 0.0, 0.0, 1.0 );
	#endif
	texColor.rgb *= backgroundIntensity;
	gl_FragColor = texColor;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,cube_vert:`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
	gl_Position.z = gl_Position.w;
}`,cube_frag:`uniform samplerCube tCube;
uniform float tFlip;
uniform float opacity;
varying vec3 vWorldDirection;
void main() {
	vec4 texColor = textureCube( tCube, vec3( tFlip * vWorldDirection.x, vWorldDirection.yz ) );
	gl_FragColor = texColor;
	gl_FragColor.a *= opacity;
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,depth_vert:`#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
varying vec2 vHighPrecisionZW;
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <skinbase_vertex>
	#include <morphinstance_vertex>
	#ifdef USE_DISPLACEMENTMAP
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vHighPrecisionZW = gl_Position.zw;
}`,depth_frag:`#if DEPTH_PACKING == 3200
	uniform float opacity;
#endif
#include <common>
#include <packing>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
varying vec2 vHighPrecisionZW;
void main() {
	vec4 diffuseColor = vec4( 1.0 );
	#include <clipping_planes_fragment>
	#if DEPTH_PACKING == 3200
		diffuseColor.a = opacity;
	#endif
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <logdepthbuf_fragment>
	#ifdef USE_REVERSED_DEPTH_BUFFER
		float fragCoordZ = vHighPrecisionZW[ 0 ] / vHighPrecisionZW[ 1 ];
	#else
		float fragCoordZ = 0.5 * vHighPrecisionZW[ 0 ] / vHighPrecisionZW[ 1 ] + 0.5;
	#endif
	#if DEPTH_PACKING == 3200
		gl_FragColor = vec4( vec3( 1.0 - fragCoordZ ), opacity );
	#elif DEPTH_PACKING == 3201
		gl_FragColor = packDepthToRGBA( fragCoordZ );
	#elif DEPTH_PACKING == 3202
		gl_FragColor = vec4( packDepthToRGB( fragCoordZ ), 1.0 );
	#elif DEPTH_PACKING == 3203
		gl_FragColor = vec4( packDepthToRG( fragCoordZ ), 0.0, 1.0 );
	#endif
}`,distance_vert:`#define DISTANCE
varying vec3 vWorldPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <skinbase_vertex>
	#include <morphinstance_vertex>
	#ifdef USE_DISPLACEMENTMAP
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <worldpos_vertex>
	#include <clipping_planes_vertex>
	vWorldPosition = worldPosition.xyz;
}`,distance_frag:`#define DISTANCE
uniform vec3 referencePosition;
uniform float nearDistance;
uniform float farDistance;
varying vec3 vWorldPosition;
#include <common>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <clipping_planes_pars_fragment>
void main () {
	vec4 diffuseColor = vec4( 1.0 );
	#include <clipping_planes_fragment>
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	float dist = length( vWorldPosition - referencePosition );
	dist = ( dist - nearDistance ) / ( farDistance - nearDistance );
	dist = saturate( dist );
	gl_FragColor = vec4( dist, 0.0, 0.0, 1.0 );
}`,equirect_vert:`varying vec3 vWorldDirection;
#include <common>
void main() {
	vWorldDirection = transformDirection( position, modelMatrix );
	#include <begin_vertex>
	#include <project_vertex>
}`,equirect_frag:`uniform sampler2D tEquirect;
varying vec3 vWorldDirection;
#include <common>
void main() {
	vec3 direction = normalize( vWorldDirection );
	vec2 sampleUV = equirectUv( direction );
	gl_FragColor = texture2D( tEquirect, sampleUV );
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
}`,linedashed_vert:`uniform float scale;
attribute float lineDistance;
varying float vLineDistance;
#include <common>
#include <uv_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	vLineDistance = scale * lineDistance;
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
}`,linedashed_frag:`uniform vec3 diffuse;
uniform float opacity;
uniform float dashSize;
uniform float totalSize;
varying float vLineDistance;
#include <common>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	if ( mod( vLineDistance, totalSize ) > dashSize ) {
		discard;
	}
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,meshbasic_vert:`#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#if defined ( USE_ENVMAP ) || defined ( USE_SKINNING )
		#include <beginnormal_vertex>
		#include <morphnormal_vertex>
		#include <skinbase_vertex>
		#include <skinnormal_vertex>
		#include <defaultnormal_vertex>
	#endif
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <fog_vertex>
}`,meshbasic_frag:`uniform vec3 diffuse;
uniform float opacity;
#ifndef FLAT_SHADED
	varying vec3 vNormal;
#endif
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <fog_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	#ifdef USE_LIGHTMAP
		vec4 lightMapTexel = texture2D( lightMap, vLightMapUv );
		reflectedLight.indirectDiffuse += lightMapTexel.rgb * lightMapIntensity * RECIPROCAL_PI;
	#else
		reflectedLight.indirectDiffuse += vec3( 1.0 );
	#endif
	#include <aomap_fragment>
	reflectedLight.indirectDiffuse *= diffuseColor.rgb;
	vec3 outgoingLight = reflectedLight.indirectDiffuse;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,meshlambert_vert:`#define LAMBERT
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,meshlambert_frag:`#define LAMBERT
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_lambert_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_lambert_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,meshmatcap_vert:`#define MATCAP
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <color_pars_vertex>
#include <displacementmap_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
	vViewPosition = - mvPosition.xyz;
}`,meshmatcap_frag:`#define MATCAP
uniform vec3 diffuse;
uniform float opacity;
uniform sampler2D matcap;
varying vec3 vViewPosition;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <normal_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	vec3 viewDir = normalize( vViewPosition );
	vec3 x = normalize( vec3( viewDir.z, 0.0, - viewDir.x ) );
	vec3 y = cross( viewDir, x );
	vec2 uv = vec2( dot( x, normal ), dot( y, normal ) ) * 0.495 + 0.5;
	#ifdef USE_MATCAP
		vec4 matcapColor = texture2D( matcap, uv );
	#else
		vec4 matcapColor = vec4( vec3( mix( 0.2, 0.8, uv.y ) ), 1.0 );
	#endif
	vec3 outgoingLight = diffuseColor.rgb * matcapColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,meshnormal_vert:`#define NORMAL
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	varying vec3 vViewPosition;
#endif
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	vViewPosition = - mvPosition.xyz;
#endif
}`,meshnormal_frag:`#define NORMAL
uniform float opacity;
#if defined( FLAT_SHADED ) || defined( USE_BUMPMAP ) || defined( USE_NORMALMAP_TANGENTSPACE )
	varying vec3 vViewPosition;
#endif
#include <uv_pars_fragment>
#include <normal_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( 0.0, 0.0, 0.0, opacity );
	#include <clipping_planes_fragment>
	#include <logdepthbuf_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	gl_FragColor = vec4( normalize( normal ) * 0.5 + 0.5, diffuseColor.a );
	#ifdef OPAQUE
		gl_FragColor.a = 1.0;
	#endif
}`,meshphong_vert:`#define PHONG
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <envmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <envmap_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,meshphong_frag:`#define PHONG
uniform vec3 diffuse;
uniform vec3 emissive;
uniform vec3 specular;
uniform float shininess;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_phong_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <specularmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <specularmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_phong_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + reflectedLight.directSpecular + reflectedLight.indirectSpecular + totalEmissiveRadiance;
	#include <envmap_fragment>
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,meshphysical_vert:`#define STANDARD
varying vec3 vViewPosition;
#ifdef USE_TRANSMISSION
	varying vec3 vWorldPosition;
#endif
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
#ifdef USE_TRANSMISSION
	vWorldPosition = worldPosition.xyz;
#endif
}`,meshphysical_frag:`#define STANDARD
#ifdef PHYSICAL
	#define IOR
	#define USE_SPECULAR
#endif
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float roughness;
uniform float metalness;
uniform float opacity;
#ifdef IOR
	uniform float ior;
#endif
#ifdef USE_SPECULAR
	uniform float specularIntensity;
	uniform vec3 specularColor;
	#ifdef USE_SPECULAR_COLORMAP
		uniform sampler2D specularColorMap;
	#endif
	#ifdef USE_SPECULAR_INTENSITYMAP
		uniform sampler2D specularIntensityMap;
	#endif
#endif
#ifdef USE_CLEARCOAT
	uniform float clearcoat;
	uniform float clearcoatRoughness;
#endif
#ifdef USE_DISPERSION
	uniform float dispersion;
#endif
#ifdef USE_IRIDESCENCE
	uniform float iridescence;
	uniform float iridescenceIOR;
	uniform float iridescenceThicknessMinimum;
	uniform float iridescenceThicknessMaximum;
#endif
#ifdef USE_SHEEN
	uniform vec3 sheenColor;
	uniform float sheenRoughness;
	#ifdef USE_SHEEN_COLORMAP
		uniform sampler2D sheenColorMap;
	#endif
	#ifdef USE_SHEEN_ROUGHNESSMAP
		uniform sampler2D sheenRoughnessMap;
	#endif
#endif
#ifdef USE_ANISOTROPY
	uniform vec2 anisotropyVector;
	#ifdef USE_ANISOTROPYMAP
		uniform sampler2D anisotropyMap;
	#endif
#endif
varying vec3 vViewPosition;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <iridescence_fragment>
#include <cube_uv_reflection_fragment>
#include <envmap_common_pars_fragment>
#include <envmap_physical_pars_fragment>
#include <fog_pars_fragment>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_physical_pars_fragment>
#include <transmission_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <clearcoat_pars_fragment>
#include <iridescence_pars_fragment>
#include <roughnessmap_pars_fragment>
#include <metalnessmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <roughnessmap_fragment>
	#include <metalnessmap_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <clearcoat_normal_fragment_begin>
	#include <clearcoat_normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_physical_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 totalDiffuse = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse;
	vec3 totalSpecular = reflectedLight.directSpecular + reflectedLight.indirectSpecular;
	#include <transmission_fragment>
	vec3 outgoingLight = totalDiffuse + totalSpecular + totalEmissiveRadiance;
	#ifdef USE_SHEEN
 
		outgoingLight = outgoingLight + sheenSpecularDirect + sheenSpecularIndirect;
 
 	#endif
	#ifdef USE_CLEARCOAT
		float dotNVcc = saturate( dot( geometryClearcoatNormal, geometryViewDir ) );
		vec3 Fcc = F_Schlick( material.clearcoatF0, material.clearcoatF90, dotNVcc );
		outgoingLight = outgoingLight * ( 1.0 - material.clearcoat * Fcc ) + ( clearcoatSpecularDirect + clearcoatSpecularIndirect ) * material.clearcoat;
	#endif
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,meshtoon_vert:`#define TOON
varying vec3 vViewPosition;
#include <common>
#include <batching_pars_vertex>
#include <uv_pars_vertex>
#include <displacementmap_pars_vertex>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <normal_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <shadowmap_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <normal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <displacementmap_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	vViewPosition = - mvPosition.xyz;
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,meshtoon_frag:`#define TOON
uniform vec3 diffuse;
uniform vec3 emissive;
uniform float opacity;
#include <common>
#include <dithering_pars_fragment>
#include <color_pars_fragment>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <aomap_pars_fragment>
#include <lightmap_pars_fragment>
#include <emissivemap_pars_fragment>
#include <gradientmap_pars_fragment>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <normal_pars_fragment>
#include <lights_toon_pars_fragment>
#include <shadowmap_pars_fragment>
#include <bumpmap_pars_fragment>
#include <normalmap_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	ReflectedLight reflectedLight = ReflectedLight( vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ), vec3( 0.0 ) );
	vec3 totalEmissiveRadiance = emissive;
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <color_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	#include <normal_fragment_begin>
	#include <normal_fragment_maps>
	#include <emissivemap_fragment>
	#include <lights_toon_fragment>
	#include <lights_fragment_begin>
	#include <lights_fragment_maps>
	#include <lights_fragment_end>
	#include <aomap_fragment>
	vec3 outgoingLight = reflectedLight.directDiffuse + reflectedLight.indirectDiffuse + totalEmissiveRadiance;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
	#include <dithering_fragment>
}`,points_vert:`uniform float size;
uniform float scale;
#include <common>
#include <color_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
#ifdef USE_POINTS_UV
	varying vec2 vUv;
	uniform mat3 uvTransform;
#endif
void main() {
	#ifdef USE_POINTS_UV
		vUv = ( uvTransform * vec3( uv, 1 ) ).xy;
	#endif
	#include <color_vertex>
	#include <morphinstance_vertex>
	#include <morphcolor_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <project_vertex>
	gl_PointSize = size;
	#ifdef USE_SIZEATTENUATION
		bool isPerspective = isPerspectiveMatrix( projectionMatrix );
		if ( isPerspective ) gl_PointSize *= ( scale / - mvPosition.z );
	#endif
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <worldpos_vertex>
	#include <fog_vertex>
}`,points_frag:`uniform vec3 diffuse;
uniform float opacity;
#include <common>
#include <color_pars_fragment>
#include <map_particle_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_particle_fragment>
	#include <color_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,shadow_vert:`#include <common>
#include <batching_pars_vertex>
#include <fog_pars_vertex>
#include <morphtarget_pars_vertex>
#include <skinning_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <shadowmap_pars_vertex>
void main() {
	#include <batching_vertex>
	#include <beginnormal_vertex>
	#include <morphinstance_vertex>
	#include <morphnormal_vertex>
	#include <skinbase_vertex>
	#include <skinnormal_vertex>
	#include <defaultnormal_vertex>
	#include <begin_vertex>
	#include <morphtarget_vertex>
	#include <skinning_vertex>
	#include <project_vertex>
	#include <logdepthbuf_vertex>
	#include <worldpos_vertex>
	#include <shadowmap_vertex>
	#include <fog_vertex>
}`,shadow_frag:`uniform vec3 color;
uniform float opacity;
#include <common>
#include <fog_pars_fragment>
#include <bsdfs>
#include <lights_pars_begin>
#include <logdepthbuf_pars_fragment>
#include <shadowmap_pars_fragment>
#include <shadowmask_pars_fragment>
void main() {
	#include <logdepthbuf_fragment>
	gl_FragColor = vec4( color, opacity * ( 1.0 - getShadowMask() ) );
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
	#include <premultiplied_alpha_fragment>
}`,sprite_vert:`uniform float rotation;
uniform vec2 center;
#include <common>
#include <uv_pars_vertex>
#include <fog_pars_vertex>
#include <logdepthbuf_pars_vertex>
#include <clipping_planes_pars_vertex>
void main() {
	#include <uv_vertex>
	vec4 mvPosition = modelViewMatrix[ 3 ];
	vec2 scale = vec2( length( modelMatrix[ 0 ].xyz ), length( modelMatrix[ 1 ].xyz ) );
	#ifndef USE_SIZEATTENUATION
		bool isPerspective = isPerspectiveMatrix( projectionMatrix );
		if ( isPerspective ) scale *= - mvPosition.z;
	#endif
	vec2 alignedPosition = ( position.xy - ( center - vec2( 0.5 ) ) ) * scale;
	vec2 rotatedPosition;
	rotatedPosition.x = cos( rotation ) * alignedPosition.x - sin( rotation ) * alignedPosition.y;
	rotatedPosition.y = sin( rotation ) * alignedPosition.x + cos( rotation ) * alignedPosition.y;
	mvPosition.xy += rotatedPosition;
	gl_Position = projectionMatrix * mvPosition;
	#include <logdepthbuf_vertex>
	#include <clipping_planes_vertex>
	#include <fog_vertex>
}`,sprite_frag:`uniform vec3 diffuse;
uniform float opacity;
#include <common>
#include <uv_pars_fragment>
#include <map_pars_fragment>
#include <alphamap_pars_fragment>
#include <alphatest_pars_fragment>
#include <alphahash_pars_fragment>
#include <fog_pars_fragment>
#include <logdepthbuf_pars_fragment>
#include <clipping_planes_pars_fragment>
void main() {
	vec4 diffuseColor = vec4( diffuse, opacity );
	#include <clipping_planes_fragment>
	vec3 outgoingLight = vec3( 0.0 );
	#include <logdepthbuf_fragment>
	#include <map_fragment>
	#include <alphamap_fragment>
	#include <alphatest_fragment>
	#include <alphahash_fragment>
	outgoingLight = diffuseColor.rgb;
	#include <opaque_fragment>
	#include <tonemapping_fragment>
	#include <colorspace_fragment>
	#include <fog_fragment>
}`},Ge={common:{diffuse:{value:new ke(16777215)},opacity:{value:1},map:{value:null},mapTransform:{value:new ht},alphaMap:{value:null},alphaMapTransform:{value:new ht},alphaTest:{value:0}},specularmap:{specularMap:{value:null},specularMapTransform:{value:new ht}},envmap:{envMap:{value:null},envMapRotation:{value:new ht},flipEnvMap:{value:-1},reflectivity:{value:1},ior:{value:1.5},refractionRatio:{value:.98},dfgLUT:{value:null}},aomap:{aoMap:{value:null},aoMapIntensity:{value:1},aoMapTransform:{value:new ht}},lightmap:{lightMap:{value:null},lightMapIntensity:{value:1},lightMapTransform:{value:new ht}},bumpmap:{bumpMap:{value:null},bumpMapTransform:{value:new ht},bumpScale:{value:1}},normalmap:{normalMap:{value:null},normalMapTransform:{value:new ht},normalScale:{value:new Te(1,1)}},displacementmap:{displacementMap:{value:null},displacementMapTransform:{value:new ht},displacementScale:{value:1},displacementBias:{value:0}},emissivemap:{emissiveMap:{value:null},emissiveMapTransform:{value:new ht}},metalnessmap:{metalnessMap:{value:null},metalnessMapTransform:{value:new ht}},roughnessmap:{roughnessMap:{value:null},roughnessMapTransform:{value:new ht}},gradientmap:{gradientMap:{value:null}},fog:{fogDensity:{value:25e-5},fogNear:{value:1},fogFar:{value:2e3},fogColor:{value:new ke(16777215)}},lights:{ambientLightColor:{value:[]},lightProbe:{value:[]},directionalLights:{value:[],properties:{direction:{},color:{}}},directionalLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{}}},directionalShadowMatrix:{value:[]},spotLights:{value:[],properties:{color:{},position:{},direction:{},distance:{},coneCos:{},penumbraCos:{},decay:{}}},spotLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{}}},spotLightMap:{value:[]},spotLightMatrix:{value:[]},pointLights:{value:[],properties:{color:{},position:{},decay:{},distance:{}}},pointLightShadows:{value:[],properties:{shadowIntensity:1,shadowBias:{},shadowNormalBias:{},shadowRadius:{},shadowMapSize:{},shadowCameraNear:{},shadowCameraFar:{}}},pointShadowMatrix:{value:[]},hemisphereLights:{value:[],properties:{direction:{},skyColor:{},groundColor:{}}},rectAreaLights:{value:[],properties:{color:{},position:{},width:{},height:{}}},ltc_1:{value:null},ltc_2:{value:null}},points:{diffuse:{value:new ke(16777215)},opacity:{value:1},size:{value:1},scale:{value:1},map:{value:null},alphaMap:{value:null},alphaMapTransform:{value:new ht},alphaTest:{value:0},uvTransform:{value:new ht}},sprite:{diffuse:{value:new ke(16777215)},opacity:{value:1},center:{value:new Te(.5,.5)},rotation:{value:0},map:{value:null},mapTransform:{value:new ht},alphaMap:{value:null},alphaMapTransform:{value:new ht},alphaTest:{value:0}}},Ci={basic:{uniforms:yn([Ge.common,Ge.specularmap,Ge.envmap,Ge.aomap,Ge.lightmap,Ge.fog]),vertexShader:pt.meshbasic_vert,fragmentShader:pt.meshbasic_frag},lambert:{uniforms:yn([Ge.common,Ge.specularmap,Ge.envmap,Ge.aomap,Ge.lightmap,Ge.emissivemap,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,Ge.fog,Ge.lights,{emissive:{value:new ke(0)},envMapIntensity:{value:1}}]),vertexShader:pt.meshlambert_vert,fragmentShader:pt.meshlambert_frag},phong:{uniforms:yn([Ge.common,Ge.specularmap,Ge.envmap,Ge.aomap,Ge.lightmap,Ge.emissivemap,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,Ge.fog,Ge.lights,{emissive:{value:new ke(0)},specular:{value:new ke(1118481)},shininess:{value:30},envMapIntensity:{value:1}}]),vertexShader:pt.meshphong_vert,fragmentShader:pt.meshphong_frag},standard:{uniforms:yn([Ge.common,Ge.envmap,Ge.aomap,Ge.lightmap,Ge.emissivemap,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,Ge.roughnessmap,Ge.metalnessmap,Ge.fog,Ge.lights,{emissive:{value:new ke(0)},roughness:{value:1},metalness:{value:0},envMapIntensity:{value:1}}]),vertexShader:pt.meshphysical_vert,fragmentShader:pt.meshphysical_frag},toon:{uniforms:yn([Ge.common,Ge.aomap,Ge.lightmap,Ge.emissivemap,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,Ge.gradientmap,Ge.fog,Ge.lights,{emissive:{value:new ke(0)}}]),vertexShader:pt.meshtoon_vert,fragmentShader:pt.meshtoon_frag},matcap:{uniforms:yn([Ge.common,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,Ge.fog,{matcap:{value:null}}]),vertexShader:pt.meshmatcap_vert,fragmentShader:pt.meshmatcap_frag},points:{uniforms:yn([Ge.points,Ge.fog]),vertexShader:pt.points_vert,fragmentShader:pt.points_frag},dashed:{uniforms:yn([Ge.common,Ge.fog,{scale:{value:1},dashSize:{value:1},totalSize:{value:2}}]),vertexShader:pt.linedashed_vert,fragmentShader:pt.linedashed_frag},depth:{uniforms:yn([Ge.common,Ge.displacementmap]),vertexShader:pt.depth_vert,fragmentShader:pt.depth_frag},normal:{uniforms:yn([Ge.common,Ge.bumpmap,Ge.normalmap,Ge.displacementmap,{opacity:{value:1}}]),vertexShader:pt.meshnormal_vert,fragmentShader:pt.meshnormal_frag},sprite:{uniforms:yn([Ge.sprite,Ge.fog]),vertexShader:pt.sprite_vert,fragmentShader:pt.sprite_frag},background:{uniforms:{uvTransform:{value:new ht},t2D:{value:null},backgroundIntensity:{value:1}},vertexShader:pt.background_vert,fragmentShader:pt.background_frag},backgroundCube:{uniforms:{envMap:{value:null},flipEnvMap:{value:-1},backgroundBlurriness:{value:0},backgroundIntensity:{value:1},backgroundRotation:{value:new ht}},vertexShader:pt.backgroundCube_vert,fragmentShader:pt.backgroundCube_frag},cube:{uniforms:{tCube:{value:null},tFlip:{value:-1},opacity:{value:1}},vertexShader:pt.cube_vert,fragmentShader:pt.cube_frag},equirect:{uniforms:{tEquirect:{value:null}},vertexShader:pt.equirect_vert,fragmentShader:pt.equirect_frag},distance:{uniforms:yn([Ge.common,Ge.displacementmap,{referencePosition:{value:new N},nearDistance:{value:1},farDistance:{value:1e3}}]),vertexShader:pt.distance_vert,fragmentShader:pt.distance_frag},shadow:{uniforms:yn([Ge.lights,Ge.fog,{color:{value:new ke(0)},opacity:{value:1}}]),vertexShader:pt.shadow_vert,fragmentShader:pt.shadow_frag}};Ci.physical={uniforms:yn([Ci.standard.uniforms,{clearcoat:{value:0},clearcoatMap:{value:null},clearcoatMapTransform:{value:new ht},clearcoatNormalMap:{value:null},clearcoatNormalMapTransform:{value:new ht},clearcoatNormalScale:{value:new Te(1,1)},clearcoatRoughness:{value:0},clearcoatRoughnessMap:{value:null},clearcoatRoughnessMapTransform:{value:new ht},dispersion:{value:0},iridescence:{value:0},iridescenceMap:{value:null},iridescenceMapTransform:{value:new ht},iridescenceIOR:{value:1.3},iridescenceThicknessMinimum:{value:100},iridescenceThicknessMaximum:{value:400},iridescenceThicknessMap:{value:null},iridescenceThicknessMapTransform:{value:new ht},sheen:{value:0},sheenColor:{value:new ke(0)},sheenColorMap:{value:null},sheenColorMapTransform:{value:new ht},sheenRoughness:{value:1},sheenRoughnessMap:{value:null},sheenRoughnessMapTransform:{value:new ht},transmission:{value:0},transmissionMap:{value:null},transmissionMapTransform:{value:new ht},transmissionSamplerSize:{value:new Te},transmissionSamplerMap:{value:null},thickness:{value:0},thicknessMap:{value:null},thicknessMapTransform:{value:new ht},attenuationDistance:{value:0},attenuationColor:{value:new ke(0)},specularColor:{value:new ke(1,1,1)},specularColorMap:{value:null},specularColorMapTransform:{value:new ht},specularIntensity:{value:1},specularIntensityMap:{value:null},specularIntensityMapTransform:{value:new ht},anisotropyVector:{value:new Te},anisotropyMap:{value:null},anisotropyMapTransform:{value:new ht}}]),vertexShader:pt.meshphysical_vert,fragmentShader:pt.meshphysical_frag};var pc={r:0,b:0,g:0},kr=new Wn,jm=new st;function Xm(r,e,t,n,i,s){let a=new ke(0),o,c,l=i===!0?0:1,h=null,p=0,f=null;function m(g){let u=g.isScene===!0?g.background:null;if(u&&u.isTexture){let y=g.backgroundBlurriness>0;u=e.get(u,y)}return u}function v(g,u){g.getRGB(pc,Ou(r)),t.buffers.color.setClear(pc.r,pc.g,pc.b,u,s)}return{getClearColor:function(){return a},setClearColor:function(g,u=1){a.set(g),l=u,v(a,l)},getClearAlpha:function(){return l},setClearAlpha:function(g){l=g,v(a,l)},render:function(g){let u=!1,y=m(g);y===null?v(a,l):y&&y.isColor&&(v(y,1),u=!0);let x=r.xr.getEnvironmentBlendMode();x==="additive"?t.buffers.color.setClear(0,0,0,1,s):x==="alpha-blend"&&t.buffers.color.setClear(0,0,0,0,s),(r.autoClear||u)&&(t.buffers.depth.setTest(!0),t.buffers.depth.setMask(!0),t.buffers.color.setMask(!0),r.clear(r.autoClearColor,r.autoClearDepth,r.autoClearStencil))},addToRenderList:function(g,u){let y=m(u);y&&(y.isCubeTexture||y.mapping===Ka)?(c===void 0&&(c=new Et(new jn(1,1,1),new qn({name:"BackgroundCubeMaterial",uniforms:zr(Ci.backgroundCube.uniforms),vertexShader:Ci.backgroundCube.vertexShader,fragmentShader:Ci.backgroundCube.fragmentShader,side:In,depthTest:!1,depthWrite:!1,fog:!1,allowOverride:!1})),c.geometry.deleteAttribute("normal"),c.geometry.deleteAttribute("uv"),c.onBeforeRender=function(x,S,E){this.matrixWorld.copyPosition(E.matrixWorld)},Object.defineProperty(c.material,"envMap",{get:function(){return this.uniforms.envMap.value}}),n.update(c)),kr.copy(u.backgroundRotation),kr.x*=-1,kr.y*=-1,kr.z*=-1,y.isCubeTexture&&y.isRenderTargetTexture===!1&&(kr.y*=-1,kr.z*=-1),c.material.uniforms.envMap.value=y,c.material.uniforms.flipEnvMap.value=y.isCubeTexture&&y.isRenderTargetTexture===!1?-1:1,c.material.uniforms.backgroundBlurriness.value=u.backgroundBlurriness,c.material.uniforms.backgroundIntensity.value=u.backgroundIntensity,c.material.uniforms.backgroundRotation.value.setFromMatrix4(jm.makeRotationFromEuler(kr)),c.material.toneMapped=Mt.getTransfer(y.colorSpace)!==bt,h===y&&p===y.version&&f===r.toneMapping||(c.material.needsUpdate=!0,h=y,p=y.version,f=r.toneMapping),c.layers.enableAll(),g.unshift(c,c.geometry,c.material,0,0,null)):y&&y.isTexture&&(o===void 0&&(o=new Et(new vn(2,2),new qn({name:"BackgroundMaterial",uniforms:zr(Ci.background.uniforms),vertexShader:Ci.background.vertexShader,fragmentShader:Ci.background.fragmentShader,side:Ns,depthTest:!1,depthWrite:!1,fog:!1,allowOverride:!1})),o.geometry.deleteAttribute("normal"),Object.defineProperty(o.material,"map",{get:function(){return this.uniforms.t2D.value}}),n.update(o)),o.material.uniforms.t2D.value=y,o.material.uniforms.backgroundIntensity.value=u.backgroundIntensity,o.material.toneMapped=Mt.getTransfer(y.colorSpace)!==bt,y.matrixAutoUpdate===!0&&y.updateMatrix(),o.material.uniforms.uvTransform.value.copy(y.matrix),h===y&&p===y.version&&f===r.toneMapping||(o.material.needsUpdate=!0,h=y,p=y.version,f=r.toneMapping),o.layers.enableAll(),g.unshift(o,o.geometry,o.material,0,0,null))},dispose:function(){c!==void 0&&(c.geometry.dispose(),c.material.dispose(),c=void 0),o!==void 0&&(o.geometry.dispose(),o.material.dispose(),o=void 0)}}}function qm(r,e){let t=r.getParameter(r.MAX_VERTEX_ATTRIBS),n={},i=l(null),s=i,a=!1;function o(y){return r.bindVertexArray(y)}function c(y){return r.deleteVertexArray(y)}function l(y){let x=[],S=[],E=[];for(let R=0;R<t;R++)x[R]=0,S[R]=0,E[R]=0;return{geometry:null,program:null,wireframe:!1,newAttributes:x,enabledAttributes:S,attributeDivisors:E,object:y,attributes:{},index:null}}function h(){let y=s.newAttributes;for(let x=0,S=y.length;x<S;x++)y[x]=0}function p(y){f(y,0)}function f(y,x){let S=s.newAttributes,E=s.enabledAttributes,R=s.attributeDivisors;S[y]=1,E[y]===0&&(r.enableVertexAttribArray(y),E[y]=1),R[y]!==x&&(r.vertexAttribDivisor(y,x),R[y]=x)}function m(){let y=s.newAttributes,x=s.enabledAttributes;for(let S=0,E=x.length;S<E;S++)x[S]!==y[S]&&(r.disableVertexAttribArray(S),x[S]=0)}function v(y,x,S,E,R,P,V){V===!0?r.vertexAttribIPointer(y,x,S,R,P):r.vertexAttribPointer(y,x,S,E,R,P)}function g(){u(),a=!0,s!==i&&(s=i,o(s.object))}function u(){i.geometry=null,i.program=null,i.wireframe=!1}return{setup:function(y,x,S,E,R){let P=!1,V=function(X,H,se,oe){let te=oe.wireframe===!0,ne=n[H.id];ne===void 0&&(ne={},n[H.id]=ne);let j=X.isInstancedMesh===!0?X.id:0,J=ne[j];J===void 0&&(J={},ne[j]=J);let ie=J[se.id];ie===void 0&&(ie={},J[se.id]=ie);let re=ie[te];return re===void 0&&(re=l(r.createVertexArray()),ie[te]=re),re}(y,E,S,x);s!==V&&(s=V,o(s.object)),P=function(X,H,se,oe){let te=s.attributes,ne=H.attributes,j=0,J=se.getAttributes();for(let ie in J)if(J[ie].location>=0){let re=te[ie],Se=ne[ie];if(Se===void 0&&(ie==="instanceMatrix"&&X.instanceMatrix&&(Se=X.instanceMatrix),ie==="instanceColor"&&X.instanceColor&&(Se=X.instanceColor)),re===void 0||re.attribute!==Se||Se&&re.data!==Se.data)return!0;j++}return s.attributesNum!==j||s.index!==oe}(y,E,S,R),P&&function(X,H,se,oe){let te={},ne=H.attributes,j=0,J=se.getAttributes();for(let ie in J)if(J[ie].location>=0){let re=ne[ie];re===void 0&&(ie==="instanceMatrix"&&X.instanceMatrix&&(re=X.instanceMatrix),ie==="instanceColor"&&X.instanceColor&&(re=X.instanceColor));let Se={};Se.attribute=re,re&&re.data&&(Se.data=re.data),te[ie]=Se,j++}s.attributes=te,s.attributesNum=j,s.index=oe}(y,E,S,R),R!==null&&e.update(R,r.ELEMENT_ARRAY_BUFFER),(P||a)&&(a=!1,function(X,H,se,oe){h();let te=oe.attributes,ne=se.getAttributes(),j=H.defaultAttributeValues;for(let J in ne){let ie=ne[J];if(ie.location>=0){let re=te[J];if(re===void 0&&(J==="instanceMatrix"&&X.instanceMatrix&&(re=X.instanceMatrix),J==="instanceColor"&&X.instanceColor&&(re=X.instanceColor)),re!==void 0){let Se=re.normalized,we=re.itemSize,ze=e.get(re);if(ze===void 0)continue;let Ae=ze.buffer,Ie=ze.type,ve=ze.bytesPerElement,Ee=Ie===r.INT||Ie===r.UNSIGNED_INT||re.gpuType===tc;if(re.isInterleavedBufferAttribute){let Le=re.data,Ye=Le.stride,We=re.offset;if(Le.isInstancedInterleavedBuffer){for(let U=0;U<ie.locationSize;U++)f(ie.location+U,Le.meshPerAttribute);X.isInstancedMesh!==!0&&oe._maxInstanceCount===void 0&&(oe._maxInstanceCount=Le.meshPerAttribute*Le.count)}else for(let U=0;U<ie.locationSize;U++)p(ie.location+U);r.bindBuffer(r.ARRAY_BUFFER,Ae);for(let U=0;U<ie.locationSize;U++)v(ie.location+U,we/ie.locationSize,Ie,Se,Ye*ve,(We+we/ie.locationSize*U)*ve,Ee)}else{if(re.isInstancedBufferAttribute){for(let Le=0;Le<ie.locationSize;Le++)f(ie.location+Le,re.meshPerAttribute);X.isInstancedMesh!==!0&&oe._maxInstanceCount===void 0&&(oe._maxInstanceCount=re.meshPerAttribute*re.count)}else for(let Le=0;Le<ie.locationSize;Le++)p(ie.location+Le);r.bindBuffer(r.ARRAY_BUFFER,Ae);for(let Le=0;Le<ie.locationSize;Le++)v(ie.location+Le,we/ie.locationSize,Ie,Se,we*ve,we/ie.locationSize*Le*ve,Ee)}}else if(j!==void 0){let Se=j[J];if(Se!==void 0)switch(Se.length){case 2:r.vertexAttrib2fv(ie.location,Se);break;case 3:r.vertexAttrib3fv(ie.location,Se);break;case 4:r.vertexAttrib4fv(ie.location,Se);break;default:r.vertexAttrib1fv(ie.location,Se)}}}}m()}(y,x,S,E),R!==null&&r.bindBuffer(r.ELEMENT_ARRAY_BUFFER,e.get(R).buffer))},reset:g,resetDefaultState:u,dispose:function(){g();for(let y in n){let x=n[y];for(let S in x){let E=x[S];for(let R in E){let P=E[R];for(let V in P)c(P[V].object),delete P[V];delete E[R]}}delete n[y]}},releaseStatesOfGeometry:function(y){if(n[y.id]===void 0)return;let x=n[y.id];for(let S in x){let E=x[S];for(let R in E){let P=E[R];for(let V in P)c(P[V].object),delete P[V];delete E[R]}}delete n[y.id]},releaseStatesOfObject:function(y){for(let x in n){let S=n[x],E=y.isInstancedMesh===!0?y.id:0,R=S[E];if(R!==void 0){for(let P in R){let V=R[P];for(let X in V)c(V[X].object),delete V[X];delete R[P]}delete S[E],Object.keys(S).length===0&&delete n[x]}}},releaseStatesOfProgram:function(y){for(let x in n){let S=n[x];for(let E in S){let R=S[E];if(R[y.id]===void 0)continue;let P=R[y.id];for(let V in P)c(P[V].object),delete P[V];delete R[y.id]}}},initAttributes:h,enableAttribute:p,disableUnusedAttributes:m}}function Ym(r,e,t){let n;function i(s,a,o){o!==0&&(r.drawArraysInstanced(n,s,a,o),t.update(a,n,o))}this.setMode=function(s){n=s},this.render=function(s,a){r.drawArrays(n,s,a),t.update(a,n,1)},this.renderInstances=i,this.renderMultiDraw=function(s,a,o){if(o===0)return;e.get("WEBGL_multi_draw").multiDrawArraysWEBGL(n,s,0,a,0,o);let c=0;for(let l=0;l<o;l++)c+=a[l];t.update(c,n,1)},this.renderMultiDrawInstances=function(s,a,o,c){if(o===0)return;let l=e.get("WEBGL_multi_draw");if(l===null)for(let h=0;h<s.length;h++)i(s[h],a[h],c[h]);else{l.multiDrawArraysInstancedWEBGL(n,s,0,a,0,c,0,o);let h=0;for(let p=0;p<o;p++)h+=a[p]*c[p];t.update(h,n,1)}}}function Zm(r,e,t,n){let i;function s(c){if(c==="highp"){if(r.getShaderPrecisionFormat(r.VERTEX_SHADER,r.HIGH_FLOAT).precision>0&&r.getShaderPrecisionFormat(r.FRAGMENT_SHADER,r.HIGH_FLOAT).precision>0)return"highp";c="mediump"}return c==="mediump"&&r.getShaderPrecisionFormat(r.VERTEX_SHADER,r.MEDIUM_FLOAT).precision>0&&r.getShaderPrecisionFormat(r.FRAGMENT_SHADER,r.MEDIUM_FLOAT).precision>0?"mediump":"lowp"}let a=t.precision!==void 0?t.precision:"highp",o=s(a);return o!==a&&(tt("WebGLRenderer:",a,"not supported, using",o,"instead."),a=o),{isWebGL2:!0,getMaxAnisotropy:function(){if(i!==void 0)return i;if(e.has("EXT_texture_filter_anisotropic")===!0){let c=e.get("EXT_texture_filter_anisotropic");i=r.getParameter(c.MAX_TEXTURE_MAX_ANISOTROPY_EXT)}else i=0;return i},getMaxPrecision:s,textureFormatReadable:function(c){return c===fi||n.convert(c)===r.getParameter(r.IMPLEMENTATION_COLOR_READ_FORMAT)},textureTypeReadable:function(c){let l=c===wi&&(e.has("EXT_color_buffer_half_float")||e.has("EXT_color_buffer_float"));return!(c!==Yn&&n.convert(c)!==r.getParameter(r.IMPLEMENTATION_COLOR_READ_TYPE)&&c!==si&&!l)},precision:a,logarithmicDepthBuffer:t.logarithmicDepthBuffer===!0,reversedDepthBuffer:t.reversedDepthBuffer===!0&&e.has("EXT_clip_control"),maxTextures:r.getParameter(r.MAX_TEXTURE_IMAGE_UNITS),maxVertexTextures:r.getParameter(r.MAX_VERTEX_TEXTURE_IMAGE_UNITS),maxTextureSize:r.getParameter(r.MAX_TEXTURE_SIZE),maxCubemapSize:r.getParameter(r.MAX_CUBE_MAP_TEXTURE_SIZE),maxAttributes:r.getParameter(r.MAX_VERTEX_ATTRIBS),maxVertexUniforms:r.getParameter(r.MAX_VERTEX_UNIFORM_VECTORS),maxVaryings:r.getParameter(r.MAX_VARYING_VECTORS),maxFragmentUniforms:r.getParameter(r.MAX_FRAGMENT_UNIFORM_VECTORS),maxSamples:r.getParameter(r.MAX_SAMPLES),samples:r.getParameter(r.SAMPLES)}}function Jm(r){let e=this,t=null,n=0,i=!1,s=!1,a=new sn,o=new ht,c={value:null,needsUpdate:!1};function l(h,p,f,m){let v=h!==null?h.length:0,g=null;if(v!==0){if(g=c.value,m!==!0||g===null){let u=f+4*v,y=p.matrixWorldInverse;o.getNormalMatrix(y),(g===null||g.length<u)&&(g=new Float32Array(u));for(let x=0,S=f;x!==v;++x,S+=4)a.copy(h[x]).applyMatrix4(y,o),a.normal.toArray(g,S),g[S+3]=a.constant}c.value=g,c.needsUpdate=!0}return e.numPlanes=v,e.numIntersection=0,g}this.uniform=c,this.numPlanes=0,this.numIntersection=0,this.init=function(h,p){let f=h.length!==0||p||n!==0||i;return i=p,n=h.length,f},this.beginShadows=function(){s=!0,l(null)},this.endShadows=function(){s=!1},this.setGlobalState=function(h,p){t=l(h,p,0)},this.setState=function(h,p,f){let m=h.clippingPlanes,v=h.clipIntersection,g=h.clipShadows,u=r.get(h);if(!i||m===null||m.length===0||s&&!g)s?l(null):function(){c.value!==t&&(c.value=t,c.needsUpdate=n>0),e.numPlanes=n,e.numIntersection=0}();else{let y=s?0:n,x=4*y,S=u.clippingState||null;c.value=S,S=l(m,p,x,f);for(let E=0;E!==x;++E)S[E]=t[E];u.clippingState=S,this.numIntersection=v?this.numPlanes:0,this.numPlanes+=y}}}var af=[.125,.215,.35,.446,.526,.582],Qa=20,eo=new Is,of=new ke,zu=null,ku=0,Hu=0,Gu=!1,Km=new N,mc=class{constructor(e){this._renderer=e,this._pingPongRenderTarget=null,this._lodMax=0,this._cubeSize=0,this._sizeLods=[],this._sigmas=[],this._lodMeshes=[],this._backgroundBox=null,this._cubemapMaterial=null,this._equirectMaterial=null,this._blurMaterial=null,this._ggxMaterial=null}fromScene(e,t=0,n=.1,i=100,s={}){let{size:a=256,position:o=Km}=s;zu=this._renderer.getRenderTarget(),ku=this._renderer.getActiveCubeFace(),Hu=this._renderer.getActiveMipmapLevel(),Gu=this._renderer.xr.enabled,this._renderer.xr.enabled=!1,this._setSize(a);let c=this._allocateTargets();return c.depthBuffer=!0,this._sceneToCubeUV(e,n,i,c,o),t>0&&this._blur(c,0,0,t),this._applyPMREM(c),this._cleanup(c),c}fromEquirectangular(e,t=null){return this._fromTexture(e,t)}fromCubemap(e,t=null){return this._fromTexture(e,t)}compileCubemapShader(){this._cubemapMaterial===null&&(this._cubemapMaterial=hf(),this._compileMaterial(this._cubemapMaterial))}compileEquirectangularShader(){this._equirectMaterial===null&&(this._equirectMaterial=cf(),this._compileMaterial(this._equirectMaterial))}dispose(){this._dispose(),this._cubemapMaterial!==null&&this._cubemapMaterial.dispose(),this._equirectMaterial!==null&&this._equirectMaterial.dispose(),this._backgroundBox!==null&&(this._backgroundBox.geometry.dispose(),this._backgroundBox.material.dispose())}_setSize(e){this._lodMax=Math.floor(Math.log2(e)),this._cubeSize=Math.pow(2,this._lodMax)}_dispose(){this._blurMaterial!==null&&this._blurMaterial.dispose(),this._ggxMaterial!==null&&this._ggxMaterial.dispose(),this._pingPongRenderTarget!==null&&this._pingPongRenderTarget.dispose();for(let e=0;e<this._lodMeshes.length;e++)this._lodMeshes[e].geometry.dispose()}_cleanup(e){this._renderer.setRenderTarget(zu,ku,Hu),this._renderer.xr.enabled=Gu,e.scissorTest=!1,Hs(e,0,0,e.width,e.height)}_fromTexture(e,t){e.mapping===Fs||e.mapping===Ur?this._setSize(e.image.length===0?16:e.image[0].width||e.image[0].image.width):this._setSize(e.image.width/4),zu=this._renderer.getRenderTarget(),ku=this._renderer.getActiveCubeFace(),Hu=this._renderer.getActiveMipmapLevel(),Gu=this._renderer.xr.enabled,this._renderer.xr.enabled=!1;let n=t||this._allocateTargets();return this._textureToCubeUV(e,n),this._applyPMREM(n),this._cleanup(n),n}_allocateTargets(){let e=3*Math.max(this._cubeSize,112),t=4*this._cubeSize,n={magFilter:nn,minFilter:nn,generateMipmaps:!1,type:wi,format:fi,colorSpace:Rr,depthBuffer:!1},i=lf(e,t,n);if(this._pingPongRenderTarget===null||this._pingPongRenderTarget.width!==e||this._pingPongRenderTarget.height!==t){this._pingPongRenderTarget!==null&&this._dispose(),this._pingPongRenderTarget=lf(e,t,n);let{_lodMax:s}=this;({lodMeshes:this._lodMeshes,sizeLods:this._sizeLods,sigmas:this._sigmas}=function(a){let o=[],c=[],l=[],h=a,p=a-4+1+af.length;for(let f=0;f<p;f++){let m=Math.pow(2,h);o.push(m);let v=1/m;f>a-4?v=af[f-a+4-1]:f===0&&(v=0),c.push(v);let g=1/(m-2),u=-g,y=1+g,x=[u,u,y,u,y,y,u,u,y,y,u,y],S=6,E=6,R=3,P=2,V=1,X=new Float32Array(R*E*S),H=new Float32Array(P*E*S),se=new Float32Array(V*E*S);for(let te=0;te<S;te++){let ne=te%3*2/3-1,j=te>2?0:-1,J=[ne,j,0,ne+2/3,j,0,ne+2/3,j+1,0,ne,j,0,ne+2/3,j+1,0,ne,j+1,0];X.set(J,R*E*te),H.set(x,P*E*te);let ie=[te,te,te,te,te,te];se.set(ie,V*E*te)}let oe=new St;oe.setAttribute("position",new on(X,R)),oe.setAttribute("uv",new on(H,P)),oe.setAttribute("faceIndex",new on(se,V)),l.push(new Et(oe,null)),h>4&&h--}return{lodMeshes:l,sizeLods:o,sigmas:c}}(s)),this._blurMaterial=function(a,o,c){let l=new Float32Array(Qa),h=new N(0,1,0);return new qn({name:"SphericalGaussianBlur",defines:{n:Qa,CUBEUV_TEXEL_WIDTH:1/o,CUBEUV_TEXEL_HEIGHT:1/c,CUBEUV_MAX_MIP:`${a}.0`},uniforms:{envMap:{value:null},samples:{value:1},weights:{value:l},latitudinal:{value:!1},dTheta:{value:0},mipInt:{value:0},poleAxis:{value:h}},vertexShader:gc(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;
			uniform int samples;
			uniform float weights[ n ];
			uniform bool latitudinal;
			uniform float dTheta;
			uniform float mipInt;
			uniform vec3 poleAxis;

			#define ENVMAP_TYPE_CUBE_UV
			#include <cube_uv_reflection_fragment>

			vec3 getSample( float theta, vec3 axis ) {

				float cosTheta = cos( theta );
				// Rodrigues' axis-angle rotation
				vec3 sampleDirection = vOutputDirection * cosTheta
					+ cross( axis, vOutputDirection ) * sin( theta )
					+ axis * dot( axis, vOutputDirection ) * ( 1.0 - cosTheta );

				return bilinearCubeUV( envMap, sampleDirection, mipInt );

			}

			void main() {

				vec3 axis = latitudinal ? poleAxis : cross( poleAxis, vOutputDirection );

				if ( all( equal( axis, vec3( 0.0 ) ) ) ) {

					axis = vec3( vOutputDirection.z, 0.0, - vOutputDirection.x );

				}

				axis = normalize( axis );

				gl_FragColor = vec4( 0.0, 0.0, 0.0, 1.0 );
				gl_FragColor.rgb += weights[ 0 ] * getSample( 0.0, axis );

				for ( int i = 1; i < n; i++ ) {

					if ( i >= samples ) {

						break;

					}

					float theta = dTheta * float( i );
					gl_FragColor.rgb += weights[ i ] * getSample( -1.0 * theta, axis );
					gl_FragColor.rgb += weights[ i ] * getSample( theta, axis );

				}

			}
		`,blending:Ei,depthTest:!1,depthWrite:!1})}(s,e,t),this._ggxMaterial=function(a,o,c){return new qn({name:"PMREMGGXConvolution",defines:{GGX_SAMPLES:256,CUBEUV_TEXEL_WIDTH:1/o,CUBEUV_TEXEL_HEIGHT:1/c,CUBEUV_MAX_MIP:`${a}.0`},uniforms:{envMap:{value:null},roughness:{value:0},mipInt:{value:0}},vertexShader:gc(),fragmentShader:`

			precision highp float;
			precision highp int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;
			uniform float roughness;
			uniform float mipInt;

			#define ENVMAP_TYPE_CUBE_UV
			#include <cube_uv_reflection_fragment>

			#define PI 3.14159265359

			// Van der Corput radical inverse
			float radicalInverse_VdC(uint bits) {
				bits = (bits << 16u) | (bits >> 16u);
				bits = ((bits & 0x55555555u) << 1u) | ((bits & 0xAAAAAAAAu) >> 1u);
				bits = ((bits & 0x33333333u) << 2u) | ((bits & 0xCCCCCCCCu) >> 2u);
				bits = ((bits & 0x0F0F0F0Fu) << 4u) | ((bits & 0xF0F0F0F0u) >> 4u);
				bits = ((bits & 0x00FF00FFu) << 8u) | ((bits & 0xFF00FF00u) >> 8u);
				return float(bits) * 2.3283064365386963e-10; // / 0x100000000
			}

			// Hammersley sequence
			vec2 hammersley(uint i, uint N) {
				return vec2(float(i) / float(N), radicalInverse_VdC(i));
			}

			// GGX VNDF importance sampling (Eric Heitz 2018)
			// "Sampling the GGX Distribution of Visible Normals"
			// https://jcgt.org/published/0007/04/01/
			vec3 importanceSampleGGX_VNDF(vec2 Xi, vec3 V, float roughness) {
				float alpha = roughness * roughness;

				// Section 4.1: Orthonormal basis
				vec3 T1 = vec3(1.0, 0.0, 0.0);
				vec3 T2 = cross(V, T1);

				// Section 4.2: Parameterization of projected area
				float r = sqrt(Xi.x);
				float phi = 2.0 * PI * Xi.y;
				float t1 = r * cos(phi);
				float t2 = r * sin(phi);
				float s = 0.5 * (1.0 + V.z);
				t2 = (1.0 - s) * sqrt(1.0 - t1 * t1) + s * t2;

				// Section 4.3: Reprojection onto hemisphere
				vec3 Nh = t1 * T1 + t2 * T2 + sqrt(max(0.0, 1.0 - t1 * t1 - t2 * t2)) * V;

				// Section 3.4: Transform back to ellipsoid configuration
				return normalize(vec3(alpha * Nh.x, alpha * Nh.y, max(0.0, Nh.z)));
			}

			void main() {
				vec3 N = normalize(vOutputDirection);
				vec3 V = N; // Assume view direction equals normal for pre-filtering

				vec3 prefilteredColor = vec3(0.0);
				float totalWeight = 0.0;

				// For very low roughness, just sample the environment directly
				if (roughness < 0.001) {
					gl_FragColor = vec4(bilinearCubeUV(envMap, N, mipInt), 1.0);
					return;
				}

				// Tangent space basis for VNDF sampling
				vec3 up = abs(N.z) < 0.999 ? vec3(0.0, 0.0, 1.0) : vec3(1.0, 0.0, 0.0);
				vec3 tangent = normalize(cross(up, N));
				vec3 bitangent = cross(N, tangent);

				for(uint i = 0u; i < uint(GGX_SAMPLES); i++) {
					vec2 Xi = hammersley(i, uint(GGX_SAMPLES));

					// For PMREM, V = N, so in tangent space V is always (0, 0, 1)
					vec3 H_tangent = importanceSampleGGX_VNDF(Xi, vec3(0.0, 0.0, 1.0), roughness);

					// Transform H back to world space
					vec3 H = normalize(tangent * H_tangent.x + bitangent * H_tangent.y + N * H_tangent.z);
					vec3 L = normalize(2.0 * dot(V, H) * H - V);

					float NdotL = max(dot(N, L), 0.0);

					if(NdotL > 0.0) {
						// Sample environment at fixed mip level
						// VNDF importance sampling handles the distribution filtering
						vec3 sampleColor = bilinearCubeUV(envMap, L, mipInt);

						// Weight by NdotL for the split-sum approximation
						// VNDF PDF naturally accounts for the visible microfacet distribution
						prefilteredColor += sampleColor * NdotL;
						totalWeight += NdotL;
					}
				}

				if (totalWeight > 0.0) {
					prefilteredColor = prefilteredColor / totalWeight;
				}

				gl_FragColor = vec4(prefilteredColor, 1.0);
			}
		`,blending:Ei,depthTest:!1,depthWrite:!1})}(s,e,t)}return i}_compileMaterial(e){let t=new Et(new St,e);this._renderer.compile(t,eo)}_sceneToCubeUV(e,t,n,i,s){let a=new an(90,1,t,n),o=[1,-1,1,1,1,1],c=[1,1,1,-1,-1,-1],l=this._renderer,h=l.autoClear,p=l.toneMapping;l.getClearColor(of),l.toneMapping=pi,l.autoClear=!1,l.state.buffers.depth.getReversed()&&(l.setRenderTarget(i),l.clearDepth(),l.setRenderTarget(null)),this._backgroundBox===null&&(this._backgroundBox=new Et(new jn,new cn({name:"PMREM.Background",side:In,depthWrite:!1,depthTest:!1})));let f=this._backgroundBox,m=f.material,v=!1,g=e.background;g?g.isColor&&(m.color.copy(g),e.background=null,v=!0):(m.color.copy(of),v=!0);for(let u=0;u<6;u++){let y=u%3;y===0?(a.up.set(0,o[u],0),a.position.set(s.x,s.y,s.z),a.lookAt(s.x+c[u],s.y,s.z)):y===1?(a.up.set(0,0,o[u]),a.position.set(s.x,s.y,s.z),a.lookAt(s.x,s.y+c[u],s.z)):(a.up.set(0,o[u],0),a.position.set(s.x,s.y,s.z),a.lookAt(s.x,s.y,s.z+c[u]));let x=this._cubeSize;Hs(i,y*x,u>2?x:0,x,x),l.setRenderTarget(i),v&&l.render(f,a),l.render(e,a)}l.toneMapping=p,l.autoClear=h,e.background=g}_textureToCubeUV(e,t){let n=this._renderer,i=e.mapping===Fs||e.mapping===Ur;i?(this._cubemapMaterial===null&&(this._cubemapMaterial=hf()),this._cubemapMaterial.uniforms.flipEnvMap.value=e.isRenderTargetTexture===!1?-1:1):this._equirectMaterial===null&&(this._equirectMaterial=cf());let s=i?this._cubemapMaterial:this._equirectMaterial,a=this._lodMeshes[0];a.material=s,s.uniforms.envMap.value=e;let o=this._cubeSize;Hs(t,0,0,3*o,2*o),n.setRenderTarget(t),n.render(a,eo)}_applyPMREM(e){let t=this._renderer,n=t.autoClear;t.autoClear=!1;let i=this._lodMeshes.length;for(let s=1;s<i;s++)this._applyGGXFilter(e,s-1,s);t.autoClear=n}_applyGGXFilter(e,t,n){let i=this._renderer,s=this._pingPongRenderTarget,a=this._ggxMaterial,o=this._lodMeshes[n];o.material=a;let c=a.uniforms,l=n/(this._lodMeshes.length-1),h=t/(this._lodMeshes.length-1),p=Math.sqrt(l*l-h*h)*(0+1.25*l),{_lodMax:f}=this,m=this._sizeLods[n],v=3*m*(n>f-4?n-f+4:0),g=4*(this._cubeSize-m);c.envMap.value=e.texture,c.roughness.value=p,c.mipInt.value=f-t,Hs(s,v,g,3*m,2*m),i.setRenderTarget(s),i.render(o,eo),c.envMap.value=s.texture,c.roughness.value=0,c.mipInt.value=f-n,Hs(e,v,g,3*m,2*m),i.setRenderTarget(e),i.render(o,eo)}_blur(e,t,n,i,s){let a=this._pingPongRenderTarget;this._halfBlur(e,a,t,n,i,"latitudinal",s),this._halfBlur(a,e,n,n,i,"longitudinal",s)}_halfBlur(e,t,n,i,s,a,o){let c=this._renderer,l=this._blurMaterial;a!=="latitudinal"&&a!=="longitudinal"&&rt("blur direction must be either latitudinal or longitudinal!");let h=this._lodMeshes[i];h.material=l;let p=l.uniforms,f=this._sizeLods[n]-1,m=isFinite(s)?Math.PI/(2*f):2*Math.PI/39,v=s/m,g=isFinite(s)?1+Math.floor(3*v):Qa;g>Qa&&tt(`sigmaRadians, ${s}, is too large and will clip, as it requested ${g} samples when the maximum is set to 20`);let u=[],y=0;for(let E=0;E<Qa;++E){let R=E/v,P=Math.exp(-R*R/2);u.push(P),E===0?y+=P:E<g&&(y+=2*P)}for(let E=0;E<u.length;E++)u[E]=u[E]/y;p.envMap.value=e.texture,p.samples.value=g,p.weights.value=u,p.latitudinal.value=a==="latitudinal",o&&(p.poleAxis.value=o);let{_lodMax:x}=this;p.dTheta.value=m,p.mipInt.value=x-n;let S=this._sizeLods[i];Hs(t,3*S*(i>x-4?i-x+4:0),4*(this._cubeSize-S),3*S,2*S),c.setRenderTarget(t),c.render(h,eo)}};function lf(r,e,t){let n=new Vn(r,e,t);return n.texture.mapping=Ka,n.texture.name="PMREM.cubeUv",n.scissorTest=!0,n}function Hs(r,e,t,n,i){r.viewport.set(e,t,n,i),r.scissor.set(e,t,n,i)}function cf(){return new qn({name:"EquirectangularToCubeUV",uniforms:{envMap:{value:null}},vertexShader:gc(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			varying vec3 vOutputDirection;

			uniform sampler2D envMap;

			#include <common>

			void main() {

				vec3 outputDirection = normalize( vOutputDirection );
				vec2 uv = equirectUv( outputDirection );

				gl_FragColor = vec4( texture2D ( envMap, uv ).rgb, 1.0 );

			}
		`,blending:Ei,depthTest:!1,depthWrite:!1})}function hf(){return new qn({name:"CubemapToCubeUV",uniforms:{envMap:{value:null},flipEnvMap:{value:-1}},vertexShader:gc(),fragmentShader:`

			precision mediump float;
			precision mediump int;

			uniform float flipEnvMap;

			varying vec3 vOutputDirection;

			uniform samplerCube envMap;

			void main() {

				gl_FragColor = textureCube( envMap, vec3( flipEnvMap * vOutputDirection.x, vOutputDirection.yz ) );

			}
		`,blending:Ei,depthTest:!1,depthWrite:!1})}function gc(){return`

		precision mediump float;
		precision mediump int;

		attribute float faceIndex;

		varying vec3 vOutputDirection;

		// RH coordinate system; PMREM face-indexing convention
		vec3 getDirection( vec2 uv, float face ) {

			uv = 2.0 * uv - 1.0;

			vec3 direction = vec3( uv, 1.0 );

			if ( face == 0.0 ) {

				direction = direction.zyx; // ( 1, v, u ) pos x

			} else if ( face == 1.0 ) {

				direction = direction.xzy;
				direction.xz *= -1.0; // ( -u, 1, -v ) pos y

			} else if ( face == 2.0 ) {

				direction.x *= -1.0; // ( -u, v, 1 ) pos z

			} else if ( face == 3.0 ) {

				direction = direction.zyx;
				direction.xz *= -1.0; // ( -1, v, -u ) neg x

			} else if ( face == 4.0 ) {

				direction = direction.xzy;
				direction.xy *= -1.0; // ( -u, -1, v ) neg y

			} else if ( face == 5.0 ) {

				direction.z *= -1.0; // ( u, v, -1 ) neg z

			}

			return direction;

		}

		void main() {

			vOutputDirection = getDirection( uv, faceIndex );
			gl_Position = vec4( position, 1.0 );

		}
	`}var _c=class extends Vn{constructor(e=1,t={}){super(e,e,t),this.isWebGLCubeRenderTarget=!0;let n={width:e,height:e,depth:1},i=[n,n,n,n,n,n];this.texture=new Ra(i),this._setTextureOptions(t),this.texture.isRenderTargetTexture=!0}fromEquirectangularTexture(e,t){this.texture.type=t.type,this.texture.colorSpace=t.colorSpace,this.texture.generateMipmaps=t.generateMipmaps,this.texture.minFilter=t.minFilter,this.texture.magFilter=t.magFilter;let n={uniforms:{tEquirect:{value:null}},vertexShader:`

				varying vec3 vWorldDirection;

				vec3 transformDirection( in vec3 dir, in mat4 matrix ) {

					return normalize( ( matrix * vec4( dir, 0.0 ) ).xyz );

				}

				void main() {

					vWorldDirection = transformDirection( position, modelMatrix );

					#include <begin_vertex>
					#include <project_vertex>

				}
			`,fragmentShader:`

				uniform sampler2D tEquirect;

				varying vec3 vWorldDirection;

				#include <common>

				void main() {

					vec3 direction = normalize( vWorldDirection );

					vec2 sampleUV = equirectUv( direction );

					gl_FragColor = texture2D( tEquirect, sampleUV );

				}
			`},i=new jn(5,5,5),s=new qn({name:"CubemapFromEquirect",uniforms:zr(n.uniforms),vertexShader:n.vertexShader,fragmentShader:n.fragmentShader,side:In,blending:Ei});s.uniforms.tEquirect.value=t;let a=new Et(i,s),o=t.minFilter;return t.minFilter===Nr&&(t.minFilter=nn),new Zl(1,10,this).update(e,a),t.minFilter=o,a.geometry.dispose(),a.material.dispose(),this}clear(e,t=!0,n=!0,i=!0){let s=e.getRenderTarget();for(let a=0;a<6;a++)e.setRenderTarget(this,a),e.clear(t,n,i);e.setRenderTarget(s)}};function $m(r){let e=new WeakMap,t=new WeakMap,n=null;function i(o,c){return c===$l?o.mapping=Fs:c===Ql&&(o.mapping=Ur),o}function s(o){let c=o.target;c.removeEventListener("dispose",s);let l=e.get(c);l!==void 0&&(e.delete(c),l.dispose())}function a(o){let c=o.target;c.removeEventListener("dispose",a);let l=t.get(c);l!==void 0&&(t.delete(c),l.dispose())}return{get:function(o,c=!1){return o==null?null:c?function(l){if(l&&l.isTexture){let h=l.mapping,p=h===$l||h===Ql,f=h===Fs||h===Ur;if(p||f){let m=t.get(l),v=m!==void 0?m.texture.pmremVersion:0;if(l.isRenderTargetTexture&&l.pmremVersion!==v)return n===null&&(n=new mc(r)),m=p?n.fromEquirectangular(l,m):n.fromCubemap(l,m),m.texture.pmremVersion=l.pmremVersion,t.set(l,m),m.texture;if(m!==void 0)return m.texture;{let g=l.image;return p&&g&&g.height>0||f&&g&&function(u){let y=0,x=6;for(let S=0;S<x;S++)u[S]!==void 0&&y++;return y===x}(g)?(n===null&&(n=new mc(r)),m=p?n.fromEquirectangular(l):n.fromCubemap(l),m.texture.pmremVersion=l.pmremVersion,t.set(l,m),l.addEventListener("dispose",a),m.texture):null}}}return l}(o):function(l){if(l&&l.isTexture){let h=l.mapping;if(h===$l||h===Ql){if(e.has(l))return i(e.get(l).texture,l.mapping);{let p=l.image;if(p&&p.height>0){let f=new _c(p.height);return f.fromEquirectangularTexture(r,l),e.set(l,f),l.addEventListener("dispose",s),i(f.texture,l.mapping)}return null}}}return l}(o)},dispose:function(){e=new WeakMap,t=new WeakMap,n!==null&&(n.dispose(),n=null)}}}function Qm(r){let e={};function t(n){if(e[n]!==void 0)return e[n];let i=r.getExtension(n);return e[n]=i,i}return{has:function(n){return t(n)!==null},init:function(){t("EXT_color_buffer_float"),t("WEBGL_clip_cull_distance"),t("OES_texture_float_linear"),t("EXT_color_buffer_half_float"),t("WEBGL_multisampled_render_to_texture"),t("WEBGL_render_shared_exponent")},get:function(n){let i=t(n);return i===null&&ya("WebGLRenderer: "+n+" extension not supported."),i}}}function e0(r,e,t,n){let i={},s=new WeakMap;function a(c){let l=c.target;l.index!==null&&e.remove(l.index);for(let p in l.attributes)e.remove(l.attributes[p]);l.removeEventListener("dispose",a),delete i[l.id];let h=s.get(l);h&&(e.remove(h),s.delete(l)),n.releaseStatesOfGeometry(l),l.isInstancedBufferGeometry===!0&&delete l._maxInstanceCount,t.memory.geometries--}function o(c){let l=[],h=c.index,p=c.attributes.position,f=0;if(p===void 0)return;if(h!==null){let g=h.array;f=h.version;for(let u=0,y=g.length;u<y;u+=3){let x=g[u+0],S=g[u+1],E=g[u+2];l.push(x,S,S,E,E,x)}}else{let g=p.array;f=p.version;for(let u=0,y=g.length/3-1;u<y;u+=3){let x=u+0,S=u+1,E=u+2;l.push(x,S,S,E,E,x)}}let m=new(p.count>=65535?Ta:Sa)(l,1);m.version=f;let v=s.get(c);v&&e.remove(v),s.set(c,m)}return{get:function(c,l){return i[l.id]===!0||(l.addEventListener("dispose",a),i[l.id]=!0,t.memory.geometries++),l},update:function(c){let l=c.attributes;for(let h in l)e.update(l[h],r.ARRAY_BUFFER)},getWireframeAttribute:function(c){let l=s.get(c);if(l){let h=c.index;h!==null&&l.version<h.version&&o(c)}else o(c);return s.get(c)}}}function t0(r,e,t){let n,i,s;function a(o,c,l){l!==0&&(r.drawElementsInstanced(n,c,i,o*s,l),t.update(c,n,l))}this.setMode=function(o){n=o},this.setIndex=function(o){i=o.type,s=o.bytesPerElement},this.render=function(o,c){r.drawElements(n,c,i,o*s),t.update(c,n,1)},this.renderInstances=a,this.renderMultiDraw=function(o,c,l){if(l===0)return;e.get("WEBGL_multi_draw").multiDrawElementsWEBGL(n,c,0,i,o,0,l);let h=0;for(let p=0;p<l;p++)h+=c[p];t.update(h,n,1)},this.renderMultiDrawInstances=function(o,c,l,h){if(l===0)return;let p=e.get("WEBGL_multi_draw");if(p===null)for(let f=0;f<o.length;f++)a(o[f]/s,c[f],h[f]);else{p.multiDrawElementsInstancedWEBGL(n,c,0,i,o,0,h,0,l);let f=0;for(let m=0;m<l;m++)f+=c[m]*h[m];t.update(f,n,1)}}}function n0(r){let e={frame:0,calls:0,triangles:0,points:0,lines:0};return{memory:{geometries:0,textures:0},render:e,programs:null,autoReset:!0,reset:function(){e.calls=0,e.triangles=0,e.points=0,e.lines=0},update:function(t,n,i){switch(e.calls++,n){case r.TRIANGLES:e.triangles+=i*(t/3);break;case r.LINES:e.lines+=i*(t/2);break;case r.LINE_STRIP:e.lines+=i*(t-1);break;case r.LINE_LOOP:e.lines+=i*t;break;case r.POINTS:e.points+=i*t;break;default:rt("WebGLInfo: Unknown draw mode:",n)}}}}function i0(r,e,t){let n=new WeakMap,i=new Lt;return{update:function(s,a,o){let c=s.morphTargetInfluences,l=a.morphAttributes.position||a.morphAttributes.normal||a.morphAttributes.color,h=l!==void 0?l.length:0,p=n.get(a);if(p===void 0||p.count!==h){let X=function(){P.dispose(),n.delete(a),a.removeEventListener("dispose",X)};p!==void 0&&p.texture.dispose();let f=a.morphAttributes.position!==void 0,m=a.morphAttributes.normal!==void 0,v=a.morphAttributes.color!==void 0,g=a.morphAttributes.position||[],u=a.morphAttributes.normal||[],y=a.morphAttributes.color||[],x=0;f===!0&&(x=1),m===!0&&(x=2),v===!0&&(x=3);let S=a.attributes.position.count*x,E=1;S>e.maxTextureSize&&(E=Math.ceil(S/e.maxTextureSize),S=e.maxTextureSize);let R=new Float32Array(S*E*4*h),P=new xa(R,S,E,h);P.type=si,P.needsUpdate=!0;let V=4*x;for(let H=0;H<h;H++){let se=g[H],oe=u[H],te=y[H],ne=S*E*4*H;for(let j=0;j<se.count;j++){let J=j*V;f===!0&&(i.fromBufferAttribute(se,j),R[ne+J+0]=i.x,R[ne+J+1]=i.y,R[ne+J+2]=i.z,R[ne+J+3]=0),m===!0&&(i.fromBufferAttribute(oe,j),R[ne+J+4]=i.x,R[ne+J+5]=i.y,R[ne+J+6]=i.z,R[ne+J+7]=0),v===!0&&(i.fromBufferAttribute(te,j),R[ne+J+8]=i.x,R[ne+J+9]=i.y,R[ne+J+10]=i.z,R[ne+J+11]=te.itemSize===4?i.w:1)}}p={count:h,texture:P,size:new Te(S,E)},n.set(a,p),a.addEventListener("dispose",X)}if(s.isInstancedMesh===!0&&s.morphTexture!==null)o.getUniforms().setValue(r,"morphTexture",s.morphTexture,t);else{let f=0;for(let v=0;v<c.length;v++)f+=c[v];let m=a.morphTargetsRelative?1:1-f;o.getUniforms().setValue(r,"morphTargetBaseInfluence",m),o.getUniforms().setValue(r,"morphTargetInfluences",c)}o.getUniforms().setValue(r,"morphTargetsTexture",p.texture,t),o.getUniforms().setValue(r,"morphTargetsTextureSize",p.size)}}}function r0(r,e,t,n,i){let s=new WeakMap;function a(o){let c=o.target;c.removeEventListener("dispose",a),n.releaseStatesOfObject(c),t.remove(c.instanceMatrix),c.instanceColor!==null&&t.remove(c.instanceColor)}return{update:function(o){let c=i.render.frame,l=o.geometry,h=e.get(o,l);if(s.get(h)!==c&&(e.update(h),s.set(h,c)),o.isInstancedMesh&&(o.hasEventListener("dispose",a)===!1&&o.addEventListener("dispose",a),s.get(o)!==c&&(t.update(o.instanceMatrix,r.ARRAY_BUFFER),o.instanceColor!==null&&t.update(o.instanceColor,r.ARRAY_BUFFER),s.set(o,c))),o.isSkinnedMesh){let p=o.skeleton;s.get(p)!==c&&(p.update(),s.set(p,c))}return h},dispose:function(){s=new WeakMap}}}var s0={[Gh]:"LINEAR_TONE_MAPPING",[Vh]:"REINHARD_TONE_MAPPING",[Wh]:"CINEON_TONE_MAPPING",[Ja]:"ACES_FILMIC_TONE_MAPPING",[Xh]:"AGX_TONE_MAPPING",[qh]:"NEUTRAL_TONE_MAPPING",[jh]:"CUSTOM_TONE_MAPPING"};function a0(r,e,t,n,i){let s=new Vn(e,t,{type:r,depthBuffer:n,stencilBuffer:i}),a=new Vn(e,t,{type:wi,depthBuffer:!1,stencilBuffer:!1}),o=new St;o.setAttribute("position",new $e([-1,3,0,-1,-1,0,3,-1,0],3)),o.setAttribute("uv",new $e([0,2,0,0,2,0],2));let c=new Nl({uniforms:{tDiffuse:{value:null}},vertexShader:`
			precision highp float;

			uniform mat4 modelViewMatrix;
			uniform mat4 projectionMatrix;

			attribute vec3 position;
			attribute vec2 uv;

			varying vec2 vUv;

			void main() {
				vUv = uv;
				gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
			}`,fragmentShader:`
			precision highp float;

			uniform sampler2D tDiffuse;

			varying vec2 vUv;

			#include <tonemapping_pars_fragment>
			#include <colorspace_pars_fragment>

			void main() {
				gl_FragColor = texture2D( tDiffuse, vUv );

				#ifdef LINEAR_TONE_MAPPING
					gl_FragColor.rgb = LinearToneMapping( gl_FragColor.rgb );
				#elif defined( REINHARD_TONE_MAPPING )
					gl_FragColor.rgb = ReinhardToneMapping( gl_FragColor.rgb );
				#elif defined( CINEON_TONE_MAPPING )
					gl_FragColor.rgb = CineonToneMapping( gl_FragColor.rgb );
				#elif defined( ACES_FILMIC_TONE_MAPPING )
					gl_FragColor.rgb = ACESFilmicToneMapping( gl_FragColor.rgb );
				#elif defined( AGX_TONE_MAPPING )
					gl_FragColor.rgb = AgXToneMapping( gl_FragColor.rgb );
				#elif defined( NEUTRAL_TONE_MAPPING )
					gl_FragColor.rgb = NeutralToneMapping( gl_FragColor.rgb );
				#elif defined( CUSTOM_TONE_MAPPING )
					gl_FragColor.rgb = CustomToneMapping( gl_FragColor.rgb );
				#endif

				#ifdef SRGB_TRANSFER
					gl_FragColor = sRGBTransferOETF( gl_FragColor );
				#endif
			}`,depthTest:!1,depthWrite:!1}),l=new Et(o,c),h=new Is(-1,1,1,-1,0,1),p,f=null,m=null,v=!1,g=null,u=[],y=!1;this.setSize=function(x,S){s.setSize(x,S),a.setSize(x,S);for(let E=0;E<u.length;E++){let R=u[E];R.setSize&&R.setSize(x,S)}},this.setEffects=function(x){u=x,y=u.length>0&&u[0].isRenderPass===!0;let S=s.width,E=s.height;for(let R=0;R<u.length;R++){let P=u[R];P.setSize&&P.setSize(S,E)}},this.begin=function(x,S){if(v||x.toneMapping===pi&&u.length===0)return!1;if(g=S,S!==null){let E=S.width,R=S.height;s.width===E&&s.height===R||this.setSize(E,R)}return y===!1&&x.setRenderTarget(s),p=x.toneMapping,x.toneMapping=pi,!0},this.hasRenderPass=function(){return y},this.end=function(x,S){x.toneMapping=p,v=!0;let E=s,R=a;for(let P=0;P<u.length;P++){let V=u[P];if(V.enabled!==!1&&(V.render(x,R,E,S),V.needsSwap!==!1)){let X=E;E=R,R=X}}if(f!==x.outputColorSpace||m!==x.toneMapping){f=x.outputColorSpace,m=x.toneMapping,c.defines={},Mt.getTransfer(f)===bt&&(c.defines.SRGB_TRANSFER="");let P=s0[m];P&&(c.defines[P]=""),c.needsUpdate=!0}c.uniforms.tDiffuse.value=E.texture,x.setRenderTarget(g),x.render(l,h),g=null,v=!1},this.isCompositing=function(){return v},this.dispose=function(){s.dispose(),a.dispose(),o.dispose(),c.dispose()}}var Rf=new _n,ju=new lr(1,1),Pf=new xa,If=new ol,Lf=new Ra,uf=[],df=[],pf=new Float32Array(16),ff=new Float32Array(9),mf=new Float32Array(4);function Vs(r,e,t){let n=r[0];if(n<=0||n>0)return r;let i=e*t,s=uf[i];if(s===void 0&&(s=new Float32Array(i),uf[i]=s),e!==0){n.toArray(s,0);for(let a=1,o=0;a!==e;++a)o+=t,r[a].toArray(s,o)}return s}function $t(r,e){if(r.length!==e.length)return!1;for(let t=0,n=r.length;t<n;t++)if(r[t]!==e[t])return!1;return!0}function Qt(r,e){for(let t=0,n=e.length;t<n;t++)r[t]=e[t]}function yc(r,e){let t=df[e];t===void 0&&(t=new Int32Array(e),df[e]=t);for(let n=0;n!==e;++n)t[n]=r.allocateTextureUnit();return t}function o0(r,e){let t=this.cache;t[0]!==e&&(r.uniform1f(this.addr,e),t[0]=e)}function l0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y||(r.uniform2f(this.addr,e.x,e.y),t[0]=e.x,t[1]=e.y);else{if($t(t,e))return;r.uniform2fv(this.addr,e),Qt(t,e)}}function c0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z||(r.uniform3f(this.addr,e.x,e.y,e.z),t[0]=e.x,t[1]=e.y,t[2]=e.z);else if(e.r!==void 0)t[0]===e.r&&t[1]===e.g&&t[2]===e.b||(r.uniform3f(this.addr,e.r,e.g,e.b),t[0]=e.r,t[1]=e.g,t[2]=e.b);else{if($t(t,e))return;r.uniform3fv(this.addr,e),Qt(t,e)}}function h0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z&&t[3]===e.w||(r.uniform4f(this.addr,e.x,e.y,e.z,e.w),t[0]=e.x,t[1]=e.y,t[2]=e.z,t[3]=e.w);else{if($t(t,e))return;r.uniform4fv(this.addr,e),Qt(t,e)}}function u0(r,e){let t=this.cache,n=e.elements;if(n===void 0){if($t(t,e))return;r.uniformMatrix2fv(this.addr,!1,e),Qt(t,e)}else{if($t(t,n))return;mf.set(n),r.uniformMatrix2fv(this.addr,!1,mf),Qt(t,n)}}function d0(r,e){let t=this.cache,n=e.elements;if(n===void 0){if($t(t,e))return;r.uniformMatrix3fv(this.addr,!1,e),Qt(t,e)}else{if($t(t,n))return;ff.set(n),r.uniformMatrix3fv(this.addr,!1,ff),Qt(t,n)}}function p0(r,e){let t=this.cache,n=e.elements;if(n===void 0){if($t(t,e))return;r.uniformMatrix4fv(this.addr,!1,e),Qt(t,e)}else{if($t(t,n))return;pf.set(n),r.uniformMatrix4fv(this.addr,!1,pf),Qt(t,n)}}function f0(r,e){let t=this.cache;t[0]!==e&&(r.uniform1i(this.addr,e),t[0]=e)}function m0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y||(r.uniform2i(this.addr,e.x,e.y),t[0]=e.x,t[1]=e.y);else{if($t(t,e))return;r.uniform2iv(this.addr,e),Qt(t,e)}}function g0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z||(r.uniform3i(this.addr,e.x,e.y,e.z),t[0]=e.x,t[1]=e.y,t[2]=e.z);else{if($t(t,e))return;r.uniform3iv(this.addr,e),Qt(t,e)}}function _0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z&&t[3]===e.w||(r.uniform4i(this.addr,e.x,e.y,e.z,e.w),t[0]=e.x,t[1]=e.y,t[2]=e.z,t[3]=e.w);else{if($t(t,e))return;r.uniform4iv(this.addr,e),Qt(t,e)}}function v0(r,e){let t=this.cache;t[0]!==e&&(r.uniform1ui(this.addr,e),t[0]=e)}function y0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y||(r.uniform2ui(this.addr,e.x,e.y),t[0]=e.x,t[1]=e.y);else{if($t(t,e))return;r.uniform2uiv(this.addr,e),Qt(t,e)}}function x0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z||(r.uniform3ui(this.addr,e.x,e.y,e.z),t[0]=e.x,t[1]=e.y,t[2]=e.z);else{if($t(t,e))return;r.uniform3uiv(this.addr,e),Qt(t,e)}}function M0(r,e){let t=this.cache;if(e.x!==void 0)t[0]===e.x&&t[1]===e.y&&t[2]===e.z&&t[3]===e.w||(r.uniform4ui(this.addr,e.x,e.y,e.z,e.w),t[0]=e.x,t[1]=e.y,t[2]=e.z,t[3]=e.w);else{if($t(t,e))return;r.uniform4uiv(this.addr,e),Qt(t,e)}}function b0(r,e,t){let n=this.cache,i=t.allocateTextureUnit(),s;n[0]!==i&&(r.uniform1i(this.addr,i),n[0]=i),this.type===r.SAMPLER_2D_SHADOW?(ju.compareFunction=t.isReversedDepthBuffer()?uc:hc,s=ju):s=Rf,t.setTexture2D(e||s,i)}function S0(r,e,t){let n=this.cache,i=t.allocateTextureUnit();n[0]!==i&&(r.uniform1i(this.addr,i),n[0]=i),t.setTexture3D(e||If,i)}function T0(r,e,t){let n=this.cache,i=t.allocateTextureUnit();n[0]!==i&&(r.uniform1i(this.addr,i),n[0]=i),t.setTextureCube(e||Lf,i)}function E0(r,e,t){let n=this.cache,i=t.allocateTextureUnit();n[0]!==i&&(r.uniform1i(this.addr,i),n[0]=i),t.setTexture2DArray(e||Pf,i)}function w0(r,e){r.uniform1fv(this.addr,e)}function A0(r,e){let t=Vs(e,this.size,2);r.uniform2fv(this.addr,t)}function C0(r,e){let t=Vs(e,this.size,3);r.uniform3fv(this.addr,t)}function R0(r,e){let t=Vs(e,this.size,4);r.uniform4fv(this.addr,t)}function P0(r,e){let t=Vs(e,this.size,4);r.uniformMatrix2fv(this.addr,!1,t)}function I0(r,e){let t=Vs(e,this.size,9);r.uniformMatrix3fv(this.addr,!1,t)}function L0(r,e){let t=Vs(e,this.size,16);r.uniformMatrix4fv(this.addr,!1,t)}function D0(r,e){r.uniform1iv(this.addr,e)}function U0(r,e){r.uniform2iv(this.addr,e)}function N0(r,e){r.uniform3iv(this.addr,e)}function O0(r,e){r.uniform4iv(this.addr,e)}function F0(r,e){r.uniform1uiv(this.addr,e)}function B0(r,e){r.uniform2uiv(this.addr,e)}function z0(r,e){r.uniform3uiv(this.addr,e)}function k0(r,e){r.uniform4uiv(this.addr,e)}function H0(r,e,t){let n=this.cache,i=e.length,s=yc(t,i),a;$t(n,s)||(r.uniform1iv(this.addr,s),Qt(n,s)),a=this.type===r.SAMPLER_2D_SHADOW?ju:Rf;for(let o=0;o!==i;++o)t.setTexture2D(e[o]||a,s[o])}function G0(r,e,t){let n=this.cache,i=e.length,s=yc(t,i);$t(n,s)||(r.uniform1iv(this.addr,s),Qt(n,s));for(let a=0;a!==i;++a)t.setTexture3D(e[a]||If,s[a])}function V0(r,e,t){let n=this.cache,i=e.length,s=yc(t,i);$t(n,s)||(r.uniform1iv(this.addr,s),Qt(n,s));for(let a=0;a!==i;++a)t.setTextureCube(e[a]||Lf,s[a])}function W0(r,e,t){let n=this.cache,i=e.length,s=yc(t,i);$t(n,s)||(r.uniform1iv(this.addr,s),Qt(n,s));for(let a=0;a!==i;++a)t.setTexture2DArray(e[a]||Pf,s[a])}var Xu=class{constructor(e,t,n){this.id=e,this.addr=n,this.cache=[],this.type=t.type,this.setValue=function(i){switch(i){case 5126:return o0;case 35664:return l0;case 35665:return c0;case 35666:return h0;case 35674:return u0;case 35675:return d0;case 35676:return p0;case 5124:case 35670:return f0;case 35667:case 35671:return m0;case 35668:case 35672:return g0;case 35669:case 35673:return _0;case 5125:return v0;case 36294:return y0;case 36295:return x0;case 36296:return M0;case 35678:case 36198:case 36298:case 36306:case 35682:return b0;case 35679:case 36299:case 36307:return S0;case 35680:case 36300:case 36308:case 36293:return T0;case 36289:case 36303:case 36311:case 36292:return E0}}(t.type)}},qu=class{constructor(e,t,n){this.id=e,this.addr=n,this.cache=[],this.type=t.type,this.size=t.size,this.setValue=function(i){switch(i){case 5126:return w0;case 35664:return A0;case 35665:return C0;case 35666:return R0;case 35674:return P0;case 35675:return I0;case 35676:return L0;case 5124:case 35670:return D0;case 35667:case 35671:return U0;case 35668:case 35672:return N0;case 35669:case 35673:return O0;case 5125:return F0;case 36294:return B0;case 36295:return z0;case 36296:return k0;case 35678:case 36198:case 36298:case 36306:case 35682:return H0;case 35679:case 36299:case 36307:return G0;case 35680:case 36300:case 36308:case 36293:return V0;case 36289:case 36303:case 36311:case 36292:return W0}}(t.type)}},Yu=class{constructor(e){this.id=e,this.seq=[],this.map={}}setValue(e,t,n){let i=this.seq;for(let s=0,a=i.length;s!==a;++s){let o=i[s];o.setValue(e,t[o.id],n)}}},Vu=/(\w+)(\])?(\[|\.)?/g;function gf(r,e){r.seq.push(e),r.map[e.id]=e}function j0(r,e,t){let n=r.name,i=n.length;for(Vu.lastIndex=0;;){let s=Vu.exec(n),a=Vu.lastIndex,o=s[1],c=s[2]==="]",l=s[3];if(c&&(o|=0),l===void 0||l==="["&&a+2===i){gf(t,l===void 0?new Xu(o,r,e):new qu(o,r,e));break}{let h=t.map[o];h===void 0&&(h=new Yu(o),gf(t,h)),t=h}}}var Gs=class{constructor(e,t){this.seq=[],this.map={};let n=e.getProgramParameter(t,e.ACTIVE_UNIFORMS);for(let a=0;a<n;++a){let o=e.getActiveUniform(t,a);j0(o,e.getUniformLocation(t,o.name),this)}let i=[],s=[];for(let a of this.seq)a.type===e.SAMPLER_2D_SHADOW||a.type===e.SAMPLER_CUBE_SHADOW||a.type===e.SAMPLER_2D_ARRAY_SHADOW?i.push(a):s.push(a);i.length>0&&(this.seq=i.concat(s))}setValue(e,t,n,i){let s=this.map[t];s!==void 0&&s.setValue(e,n,i)}setOptional(e,t,n){let i=t[n];i!==void 0&&this.setValue(e,n,i)}static upload(e,t,n,i){for(let s=0,a=t.length;s!==a;++s){let o=t[s],c=n[o.id];c.needsUpdate!==!1&&o.setValue(e,c.value,i)}}static seqWithValue(e,t){let n=[];for(let i=0,s=e.length;i!==s;++i){let a=e[i];a.id in t&&n.push(a)}return n}};function _f(r,e,t){let n=r.createShader(e);return r.shaderSource(n,t),r.compileShader(n),n}var X0=0,vf=new ht;function yf(r,e,t){let n=r.getShaderParameter(e,r.COMPILE_STATUS),i=(r.getShaderInfoLog(e)||"").trim();if(n&&i==="")return"";let s=/ERROR: 0:(\d+)/.exec(i);if(s){let a=parseInt(s[1]);return t.toUpperCase()+`

`+i+`

`+function(o,c){let l=o.split(`
`),h=[],p=Math.max(c-6,0),f=Math.min(c+6,l.length);for(let m=p;m<f;m++){let v=m+1;h.push(`${v===c?">":" "} ${v}: ${l[m]}`)}return h.join(`
`)}(r.getShaderSource(e),a)}return i}function q0(r,e){let t=function(n){Mt._getMatrix(vf,Mt.workingColorSpace,n);let i=`mat3( ${vf.elements.map(s=>s.toFixed(4))} )`;switch(Mt.getTransfer(n)){case va:return[i,"LinearTransferOETF"];case bt:return[i,"sRGBTransferOETF"];default:return tt("WebGLProgram: Unsupported color space: ",n),[i,"LinearTransferOETF"]}}(e);return[`vec4 ${r}( vec4 value ) {`,`	return ${t[1]}( vec4( value.rgb * ${t[0]}, value.a ) );`,"}"].join(`
`)}var Y0={[Gh]:"Linear",[Vh]:"Reinhard",[Wh]:"Cineon",[Ja]:"ACESFilmic",[Xh]:"AgX",[qh]:"Neutral",[jh]:"Custom"};function Z0(r,e){let t=Y0[e];return t===void 0?(tt("WebGLProgram: Unsupported toneMapping:",e),"vec3 "+r+"( vec3 color ) { return LinearToneMapping( color ); }"):"vec3 "+r+"( vec3 color ) { return "+t+"ToneMapping( color ); }"}var fc=new N;function J0(){return Mt.getLuminanceCoefficients(fc),["float luminance( const in vec3 rgb ) {",`	const vec3 weights = vec3( ${fc.x.toFixed(4)}, ${fc.y.toFixed(4)}, ${fc.z.toFixed(4)} );`,"	return dot( weights, rgb );","}"].join(`
`)}function to(r){return r!==""}function xf(r,e){let t=e.numSpotLightShadows+e.numSpotLightMaps-e.numSpotLightShadowsWithMaps;return r.replace(/NUM_DIR_LIGHTS/g,e.numDirLights).replace(/NUM_SPOT_LIGHTS/g,e.numSpotLights).replace(/NUM_SPOT_LIGHT_MAPS/g,e.numSpotLightMaps).replace(/NUM_SPOT_LIGHT_COORDS/g,t).replace(/NUM_RECT_AREA_LIGHTS/g,e.numRectAreaLights).replace(/NUM_POINT_LIGHTS/g,e.numPointLights).replace(/NUM_HEMI_LIGHTS/g,e.numHemiLights).replace(/NUM_DIR_LIGHT_SHADOWS/g,e.numDirLightShadows).replace(/NUM_SPOT_LIGHT_SHADOWS_WITH_MAPS/g,e.numSpotLightShadowsWithMaps).replace(/NUM_SPOT_LIGHT_SHADOWS/g,e.numSpotLightShadows).replace(/NUM_POINT_LIGHT_SHADOWS/g,e.numPointLightShadows)}function Mf(r,e){return r.replace(/NUM_CLIPPING_PLANES/g,e.numClippingPlanes).replace(/UNION_CLIPPING_PLANES/g,e.numClippingPlanes-e.numClipIntersection)}var K0=/^[ \t]*#include +<([\w\d./]+)>/gm;function Zu(r){return r.replace(K0,Q0)}var $0=new Map;function Q0(r,e){let t=pt[e];if(t===void 0){let n=$0.get(e);if(n===void 0)throw new Error("Can not resolve #include <"+e+">");t=pt[n],tt('WebGLRenderer: Shader chunk "%s" has been deprecated. Use "%s" instead.',e,n)}return Zu(t)}var eg=/#pragma unroll_loop_start\s+for\s*\(\s*int\s+i\s*=\s*(\d+)\s*;\s*i\s*<\s*(\d+)\s*;\s*i\s*\+\+\s*\)\s*{([\s\S]+?)}\s+#pragma unroll_loop_end/g;function bf(r){return r.replace(eg,tg)}function tg(r,e,t,n){let i="";for(let s=parseInt(e);s<parseInt(t);s++)i+=n.replace(/\[\s*i\s*\]/g,"[ "+s+" ]").replace(/UNROLLED_LOOP_INDEX/g,s);return i}function Sf(r){let e=`precision ${r.precision} float;
	precision ${r.precision} int;
	precision ${r.precision} sampler2D;
	precision ${r.precision} samplerCube;
	precision ${r.precision} sampler3D;
	precision ${r.precision} sampler2DArray;
	precision ${r.precision} sampler2DShadow;
	precision ${r.precision} samplerCubeShadow;
	precision ${r.precision} sampler2DArrayShadow;
	precision ${r.precision} isampler2D;
	precision ${r.precision} isampler3D;
	precision ${r.precision} isamplerCube;
	precision ${r.precision} isampler2DArray;
	precision ${r.precision} usampler2D;
	precision ${r.precision} usampler3D;
	precision ${r.precision} usamplerCube;
	precision ${r.precision} usampler2DArray;
	`;return r.precision==="highp"?e+=`
#define HIGH_PRECISION`:r.precision==="mediump"?e+=`
#define MEDIUM_PRECISION`:r.precision==="lowp"&&(e+=`
#define LOW_PRECISION`),e}var ng={[Dr]:"SHADOWMAP_TYPE_PCF",[Us]:"SHADOWMAP_TYPE_VSM"},ig={[Fs]:"ENVMAP_TYPE_CUBE",[Ur]:"ENVMAP_TYPE_CUBE",[Ka]:"ENVMAP_TYPE_CUBE_UV"},rg={[Ur]:"ENVMAP_MODE_REFRACTION"},sg={[Np]:"ENVMAP_BLENDING_MULTIPLY",[Op]:"ENVMAP_BLENDING_MIX",[Fp]:"ENVMAP_BLENDING_ADD"};function ag(r,e,t,n){let i=r.getContext(),s=t.defines,a=t.vertexShader,o=t.fragmentShader,c=function(oe){return ng[oe.shadowMapType]||"SHADOWMAP_TYPE_BASIC"}(t),l=function(oe){return oe.envMap===!1?"ENVMAP_TYPE_CUBE":ig[oe.envMapMode]||"ENVMAP_TYPE_CUBE"}(t),h=function(oe){return oe.envMap===!1?"ENVMAP_MODE_REFLECTION":rg[oe.envMapMode]||"ENVMAP_MODE_REFLECTION"}(t),p=function(oe){return oe.envMap===!1?"ENVMAP_BLENDING_NONE":sg[oe.combine]||"ENVMAP_BLENDING_NONE"}(t),f=function(oe){let te=oe.envMapCubeUVHeight;if(te===null)return null;let ne=Math.log2(te)-2,j=1/te;return{texelWidth:1/(3*Math.max(Math.pow(2,ne),112)),texelHeight:j,maxMip:ne}}(t),m=function(oe){return[oe.extensionClipCullDistance?"#extension GL_ANGLE_clip_cull_distance : require":"",oe.extensionMultiDraw?"#extension GL_ANGLE_multi_draw : require":""].filter(to).join(`
`)}(t),v=function(oe){let te=[];for(let ne in oe){let j=oe[ne];j!==!1&&te.push("#define "+ne+" "+j)}return te.join(`
`)}(s),g=i.createProgram(),u,y,x=t.glslVersion?"#version "+t.glslVersion+`
`:"";t.isRawShaderMaterial?(u=["#define SHADER_TYPE "+t.shaderType,"#define SHADER_NAME "+t.shaderName,v].filter(to).join(`
`),u.length>0&&(u+=`
`),y=["#define SHADER_TYPE "+t.shaderType,"#define SHADER_NAME "+t.shaderName,v].filter(to).join(`
`),y.length>0&&(y+=`
`)):(u=[Sf(t),"#define SHADER_TYPE "+t.shaderType,"#define SHADER_NAME "+t.shaderName,v,t.extensionClipCullDistance?"#define USE_CLIP_DISTANCE":"",t.batching?"#define USE_BATCHING":"",t.batchingColor?"#define USE_BATCHING_COLOR":"",t.instancing?"#define USE_INSTANCING":"",t.instancingColor?"#define USE_INSTANCING_COLOR":"",t.instancingMorph?"#define USE_INSTANCING_MORPH":"",t.useFog&&t.fog?"#define USE_FOG":"",t.useFog&&t.fogExp2?"#define FOG_EXP2":"",t.map?"#define USE_MAP":"",t.envMap?"#define USE_ENVMAP":"",t.envMap?"#define "+h:"",t.lightMap?"#define USE_LIGHTMAP":"",t.aoMap?"#define USE_AOMAP":"",t.bumpMap?"#define USE_BUMPMAP":"",t.normalMap?"#define USE_NORMALMAP":"",t.normalMapObjectSpace?"#define USE_NORMALMAP_OBJECTSPACE":"",t.normalMapTangentSpace?"#define USE_NORMALMAP_TANGENTSPACE":"",t.displacementMap?"#define USE_DISPLACEMENTMAP":"",t.emissiveMap?"#define USE_EMISSIVEMAP":"",t.anisotropy?"#define USE_ANISOTROPY":"",t.anisotropyMap?"#define USE_ANISOTROPYMAP":"",t.clearcoatMap?"#define USE_CLEARCOATMAP":"",t.clearcoatRoughnessMap?"#define USE_CLEARCOAT_ROUGHNESSMAP":"",t.clearcoatNormalMap?"#define USE_CLEARCOAT_NORMALMAP":"",t.iridescenceMap?"#define USE_IRIDESCENCEMAP":"",t.iridescenceThicknessMap?"#define USE_IRIDESCENCE_THICKNESSMAP":"",t.specularMap?"#define USE_SPECULARMAP":"",t.specularColorMap?"#define USE_SPECULAR_COLORMAP":"",t.specularIntensityMap?"#define USE_SPECULAR_INTENSITYMAP":"",t.roughnessMap?"#define USE_ROUGHNESSMAP":"",t.metalnessMap?"#define USE_METALNESSMAP":"",t.alphaMap?"#define USE_ALPHAMAP":"",t.alphaHash?"#define USE_ALPHAHASH":"",t.transmission?"#define USE_TRANSMISSION":"",t.transmissionMap?"#define USE_TRANSMISSIONMAP":"",t.thicknessMap?"#define USE_THICKNESSMAP":"",t.sheenColorMap?"#define USE_SHEEN_COLORMAP":"",t.sheenRoughnessMap?"#define USE_SHEEN_ROUGHNESSMAP":"",t.mapUv?"#define MAP_UV "+t.mapUv:"",t.alphaMapUv?"#define ALPHAMAP_UV "+t.alphaMapUv:"",t.lightMapUv?"#define LIGHTMAP_UV "+t.lightMapUv:"",t.aoMapUv?"#define AOMAP_UV "+t.aoMapUv:"",t.emissiveMapUv?"#define EMISSIVEMAP_UV "+t.emissiveMapUv:"",t.bumpMapUv?"#define BUMPMAP_UV "+t.bumpMapUv:"",t.normalMapUv?"#define NORMALMAP_UV "+t.normalMapUv:"",t.displacementMapUv?"#define DISPLACEMENTMAP_UV "+t.displacementMapUv:"",t.metalnessMapUv?"#define METALNESSMAP_UV "+t.metalnessMapUv:"",t.roughnessMapUv?"#define ROUGHNESSMAP_UV "+t.roughnessMapUv:"",t.anisotropyMapUv?"#define ANISOTROPYMAP_UV "+t.anisotropyMapUv:"",t.clearcoatMapUv?"#define CLEARCOATMAP_UV "+t.clearcoatMapUv:"",t.clearcoatNormalMapUv?"#define CLEARCOAT_NORMALMAP_UV "+t.clearcoatNormalMapUv:"",t.clearcoatRoughnessMapUv?"#define CLEARCOAT_ROUGHNESSMAP_UV "+t.clearcoatRoughnessMapUv:"",t.iridescenceMapUv?"#define IRIDESCENCEMAP_UV "+t.iridescenceMapUv:"",t.iridescenceThicknessMapUv?"#define IRIDESCENCE_THICKNESSMAP_UV "+t.iridescenceThicknessMapUv:"",t.sheenColorMapUv?"#define SHEEN_COLORMAP_UV "+t.sheenColorMapUv:"",t.sheenRoughnessMapUv?"#define SHEEN_ROUGHNESSMAP_UV "+t.sheenRoughnessMapUv:"",t.specularMapUv?"#define SPECULARMAP_UV "+t.specularMapUv:"",t.specularColorMapUv?"#define SPECULAR_COLORMAP_UV "+t.specularColorMapUv:"",t.specularIntensityMapUv?"#define SPECULAR_INTENSITYMAP_UV "+t.specularIntensityMapUv:"",t.transmissionMapUv?"#define TRANSMISSIONMAP_UV "+t.transmissionMapUv:"",t.thicknessMapUv?"#define THICKNESSMAP_UV "+t.thicknessMapUv:"",t.vertexTangents&&t.flatShading===!1?"#define USE_TANGENT":"",t.vertexColors?"#define USE_COLOR":"",t.vertexAlphas?"#define USE_COLOR_ALPHA":"",t.vertexUv1s?"#define USE_UV1":"",t.vertexUv2s?"#define USE_UV2":"",t.vertexUv3s?"#define USE_UV3":"",t.pointsUvs?"#define USE_POINTS_UV":"",t.flatShading?"#define FLAT_SHADED":"",t.skinning?"#define USE_SKINNING":"",t.morphTargets?"#define USE_MORPHTARGETS":"",t.morphNormals&&t.flatShading===!1?"#define USE_MORPHNORMALS":"",t.morphColors?"#define USE_MORPHCOLORS":"",t.morphTargetsCount>0?"#define MORPHTARGETS_TEXTURE_STRIDE "+t.morphTextureStride:"",t.morphTargetsCount>0?"#define MORPHTARGETS_COUNT "+t.morphTargetsCount:"",t.doubleSided?"#define DOUBLE_SIDED":"",t.flipSided?"#define FLIP_SIDED":"",t.shadowMapEnabled?"#define USE_SHADOWMAP":"",t.shadowMapEnabled?"#define "+c:"",t.sizeAttenuation?"#define USE_SIZEATTENUATION":"",t.numLightProbes>0?"#define USE_LIGHT_PROBES":"",t.logarithmicDepthBuffer?"#define USE_LOGARITHMIC_DEPTH_BUFFER":"",t.reversedDepthBuffer?"#define USE_REVERSED_DEPTH_BUFFER":"","uniform mat4 modelMatrix;","uniform mat4 modelViewMatrix;","uniform mat4 projectionMatrix;","uniform mat4 viewMatrix;","uniform mat3 normalMatrix;","uniform vec3 cameraPosition;","uniform bool isOrthographic;","#ifdef USE_INSTANCING","	attribute mat4 instanceMatrix;","#endif","#ifdef USE_INSTANCING_COLOR","	attribute vec3 instanceColor;","#endif","#ifdef USE_INSTANCING_MORPH","	uniform sampler2D morphTexture;","#endif","attribute vec3 position;","attribute vec3 normal;","attribute vec2 uv;","#ifdef USE_UV1","	attribute vec2 uv1;","#endif","#ifdef USE_UV2","	attribute vec2 uv2;","#endif","#ifdef USE_UV3","	attribute vec2 uv3;","#endif","#ifdef USE_TANGENT","	attribute vec4 tangent;","#endif","#if defined( USE_COLOR_ALPHA )","	attribute vec4 color;","#elif defined( USE_COLOR )","	attribute vec3 color;","#endif","#ifdef USE_SKINNING","	attribute vec4 skinIndex;","	attribute vec4 skinWeight;","#endif",`
`].filter(to).join(`
`),y=[Sf(t),"#define SHADER_TYPE "+t.shaderType,"#define SHADER_NAME "+t.shaderName,v,t.useFog&&t.fog?"#define USE_FOG":"",t.useFog&&t.fogExp2?"#define FOG_EXP2":"",t.alphaToCoverage?"#define ALPHA_TO_COVERAGE":"",t.map?"#define USE_MAP":"",t.matcap?"#define USE_MATCAP":"",t.envMap?"#define USE_ENVMAP":"",t.envMap?"#define "+l:"",t.envMap?"#define "+h:"",t.envMap?"#define "+p:"",f?"#define CUBEUV_TEXEL_WIDTH "+f.texelWidth:"",f?"#define CUBEUV_TEXEL_HEIGHT "+f.texelHeight:"",f?"#define CUBEUV_MAX_MIP "+f.maxMip+".0":"",t.lightMap?"#define USE_LIGHTMAP":"",t.aoMap?"#define USE_AOMAP":"",t.bumpMap?"#define USE_BUMPMAP":"",t.normalMap?"#define USE_NORMALMAP":"",t.normalMapObjectSpace?"#define USE_NORMALMAP_OBJECTSPACE":"",t.normalMapTangentSpace?"#define USE_NORMALMAP_TANGENTSPACE":"",t.emissiveMap?"#define USE_EMISSIVEMAP":"",t.anisotropy?"#define USE_ANISOTROPY":"",t.anisotropyMap?"#define USE_ANISOTROPYMAP":"",t.clearcoat?"#define USE_CLEARCOAT":"",t.clearcoatMap?"#define USE_CLEARCOATMAP":"",t.clearcoatRoughnessMap?"#define USE_CLEARCOAT_ROUGHNESSMAP":"",t.clearcoatNormalMap?"#define USE_CLEARCOAT_NORMALMAP":"",t.dispersion?"#define USE_DISPERSION":"",t.iridescence?"#define USE_IRIDESCENCE":"",t.iridescenceMap?"#define USE_IRIDESCENCEMAP":"",t.iridescenceThicknessMap?"#define USE_IRIDESCENCE_THICKNESSMAP":"",t.specularMap?"#define USE_SPECULARMAP":"",t.specularColorMap?"#define USE_SPECULAR_COLORMAP":"",t.specularIntensityMap?"#define USE_SPECULAR_INTENSITYMAP":"",t.roughnessMap?"#define USE_ROUGHNESSMAP":"",t.metalnessMap?"#define USE_METALNESSMAP":"",t.alphaMap?"#define USE_ALPHAMAP":"",t.alphaTest?"#define USE_ALPHATEST":"",t.alphaHash?"#define USE_ALPHAHASH":"",t.sheen?"#define USE_SHEEN":"",t.sheenColorMap?"#define USE_SHEEN_COLORMAP":"",t.sheenRoughnessMap?"#define USE_SHEEN_ROUGHNESSMAP":"",t.transmission?"#define USE_TRANSMISSION":"",t.transmissionMap?"#define USE_TRANSMISSIONMAP":"",t.thicknessMap?"#define USE_THICKNESSMAP":"",t.vertexTangents&&t.flatShading===!1?"#define USE_TANGENT":"",t.vertexColors||t.instancingColor?"#define USE_COLOR":"",t.vertexAlphas||t.batchingColor?"#define USE_COLOR_ALPHA":"",t.vertexUv1s?"#define USE_UV1":"",t.vertexUv2s?"#define USE_UV2":"",t.vertexUv3s?"#define USE_UV3":"",t.pointsUvs?"#define USE_POINTS_UV":"",t.gradientMap?"#define USE_GRADIENTMAP":"",t.flatShading?"#define FLAT_SHADED":"",t.doubleSided?"#define DOUBLE_SIDED":"",t.flipSided?"#define FLIP_SIDED":"",t.shadowMapEnabled?"#define USE_SHADOWMAP":"",t.shadowMapEnabled?"#define "+c:"",t.premultipliedAlpha?"#define PREMULTIPLIED_ALPHA":"",t.numLightProbes>0?"#define USE_LIGHT_PROBES":"",t.decodeVideoTexture?"#define DECODE_VIDEO_TEXTURE":"",t.decodeVideoTextureEmissive?"#define DECODE_VIDEO_TEXTURE_EMISSIVE":"",t.logarithmicDepthBuffer?"#define USE_LOGARITHMIC_DEPTH_BUFFER":"",t.reversedDepthBuffer?"#define USE_REVERSED_DEPTH_BUFFER":"","uniform mat4 viewMatrix;","uniform vec3 cameraPosition;","uniform bool isOrthographic;",t.toneMapping!==pi?"#define TONE_MAPPING":"",t.toneMapping!==pi?pt.tonemapping_pars_fragment:"",t.toneMapping!==pi?Z0("toneMapping",t.toneMapping):"",t.dithering?"#define DITHERING":"",t.opaque?"#define OPAQUE":"",pt.colorspace_pars_fragment,q0("linearToOutputTexel",t.outputColorSpace),J0(),t.useDepthPacking?"#define DEPTH_PACKING "+t.depthPacking:"",`
`].filter(to).join(`
`)),a=Zu(a),a=xf(a,t),a=Mf(a,t),o=Zu(o),o=xf(o,t),o=Mf(o,t),a=bf(a),o=bf(o),t.isRawShaderMaterial!==!0&&(x=`#version 300 es
`,u=[m,"#define attribute in","#define varying out","#define texture2D texture"].join(`
`)+`
`+u,y=["#define varying in",t.glslVersion===Du?"":"layout(location = 0) out highp vec4 pc_fragColor;",t.glslVersion===Du?"":"#define gl_FragColor pc_fragColor","#define gl_FragDepthEXT gl_FragDepth","#define texture2D texture","#define textureCube texture","#define texture2DProj textureProj","#define texture2DLodEXT textureLod","#define texture2DProjLodEXT textureProjLod","#define textureCubeLodEXT textureLod","#define texture2DGradEXT textureGrad","#define texture2DProjGradEXT textureProjGrad","#define textureCubeGradEXT textureGrad"].join(`
`)+`
`+y);let S=x+u+a,E=x+y+o,R=_f(i,i.VERTEX_SHADER,S),P=_f(i,i.FRAGMENT_SHADER,E);function V(oe){if(r.debug.checkShaderErrors){let te=i.getProgramInfoLog(g)||"",ne=i.getShaderInfoLog(R)||"",j=i.getShaderInfoLog(P)||"",J=te.trim(),ie=ne.trim(),re=j.trim(),Se=!0,we=!0;if(i.getProgramParameter(g,i.LINK_STATUS)===!1)if(Se=!1,typeof r.debug.onShaderError=="function")r.debug.onShaderError(i,g,R,P);else{let ze=yf(i,R,"vertex"),Ae=yf(i,P,"fragment");rt("THREE.WebGLProgram: Shader Error "+i.getError()+" - VALIDATE_STATUS "+i.getProgramParameter(g,i.VALIDATE_STATUS)+`

Material Name: `+oe.name+`
Material Type: `+oe.type+`

Program Info Log: `+J+`
`+ze+`
`+Ae)}else J!==""?tt("WebGLProgram: Program Info Log:",J):ie!==""&&re!==""||(we=!1);we&&(oe.diagnostics={runnable:Se,programLog:J,vertexShader:{log:ie,prefix:u},fragmentShader:{log:re,prefix:y}})}i.deleteShader(R),i.deleteShader(P),X=new Gs(i,g),H=function(te,ne){let j={},J=te.getProgramParameter(ne,te.ACTIVE_ATTRIBUTES);for(let ie=0;ie<J;ie++){let re=te.getActiveAttrib(ne,ie),Se=re.name,we=1;re.type===te.FLOAT_MAT2&&(we=2),re.type===te.FLOAT_MAT3&&(we=3),re.type===te.FLOAT_MAT4&&(we=4),j[Se]={type:re.type,location:te.getAttribLocation(ne,Se),locationSize:we}}return j}(i,g)}let X,H;i.attachShader(g,R),i.attachShader(g,P),t.index0AttributeName!==void 0?i.bindAttribLocation(g,0,t.index0AttributeName):t.morphTargets===!0&&i.bindAttribLocation(g,0,"position"),i.linkProgram(g),this.getUniforms=function(){return X===void 0&&V(this),X},this.getAttributes=function(){return H===void 0&&V(this),H};let se=t.rendererExtensionParallelShaderCompile===!1;return this.isReady=function(){return se===!1&&(se=i.getProgramParameter(g,37297)),se},this.destroy=function(){n.releaseStatesOfProgram(this),i.deleteProgram(g),this.program=void 0},this.type=t.shaderType,this.name=t.shaderName,this.id=X0++,this.cacheKey=e,this.usedTimes=1,this.program=g,this.vertexShader=R,this.fragmentShader=P,this}var og=0,Ju=class{constructor(){this.shaderCache=new Map,this.materialCache=new Map}update(e){let t=e.vertexShader,n=e.fragmentShader,i=this._getShaderStage(t),s=this._getShaderStage(n),a=this._getShaderCacheForMaterial(e);return a.has(i)===!1&&(a.add(i),i.usedTimes++),a.has(s)===!1&&(a.add(s),s.usedTimes++),this}remove(e){let t=this.materialCache.get(e);for(let n of t)n.usedTimes--,n.usedTimes===0&&this.shaderCache.delete(n.code);return this.materialCache.delete(e),this}getVertexShaderID(e){return this._getShaderStage(e.vertexShader).id}getFragmentShaderID(e){return this._getShaderStage(e.fragmentShader).id}dispose(){this.shaderCache.clear(),this.materialCache.clear()}_getShaderCacheForMaterial(e){let t=this.materialCache,n=t.get(e);return n===void 0&&(n=new Set,t.set(e,n)),n}_getShaderStage(e){let t=this.shaderCache,n=t.get(e);return n===void 0&&(n=new Ku(e),t.set(e,n)),n}},Ku=class{constructor(e){this.id=og++,this.code=e,this.usedTimes=0}};function lg(r,e,t,n,i,s){let a=new Ms,o=new Ju,c=new Set,l=[],h=new Map,p=n.logarithmicDepthBuffer,f=n.precision,m={MeshDepthMaterial:"depth",MeshDistanceMaterial:"distance",MeshNormalMaterial:"normal",MeshBasicMaterial:"basic",MeshLambertMaterial:"lambert",MeshPhongMaterial:"phong",MeshToonMaterial:"toon",MeshStandardMaterial:"physical",MeshPhysicalMaterial:"physical",MeshMatcapMaterial:"matcap",LineBasicMaterial:"basic",LineDashedMaterial:"dashed",PointsMaterial:"points",ShadowMaterial:"shadow",SpriteMaterial:"sprite"};function v(g){return c.add(g),g===0?"uv":`uv${g}`}return{getParameters:function(g,u,y,x,S){let E=x.fog,R=S.geometry,P=g.isMeshStandardMaterial||g.isMeshLambertMaterial||g.isMeshPhongMaterial?x.environment:null,V=g.isMeshStandardMaterial||g.isMeshLambertMaterial&&!g.envMap||g.isMeshPhongMaterial&&!g.envMap,X=e.get(g.envMap||P,V),H=X&&X.mapping===Ka?X.image.height:null,se=m[g.type];g.precision!==null&&(f=n.getMaxPrecision(g.precision),f!==g.precision&&tt("WebGLProgram.getParameters:",g.precision,"not supported, using",f,"instead."));let oe=R.morphAttributes.position||R.morphAttributes.normal||R.morphAttributes.color,te=oe!==void 0?oe.length:0,ne,j,J,ie,re=0;if(R.morphAttributes.position!==void 0&&(re=1),R.morphAttributes.normal!==void 0&&(re=2),R.morphAttributes.color!==void 0&&(re=3),se){let nt=Ci[se];ne=nt.vertexShader,j=nt.fragmentShader}else ne=g.vertexShader,j=g.fragmentShader,o.update(g),J=o.getVertexShaderID(g),ie=o.getFragmentShaderID(g);let Se=r.getRenderTarget(),we=r.state.buffers.depth.getReversed(),ze=S.isInstancedMesh===!0,Ae=S.isBatchedMesh===!0,Ie=!!g.map,ve=!!g.matcap,Ee=!!X,Le=!!g.aoMap,Ye=!!g.lightMap,We=!!g.bumpMap,U=!!g.normalMap,D=!!g.displacementMap,$=!!g.emissiveMap,B=!!g.metalnessMap,w=!!g.roughnessMap,k=g.anisotropy>0,Y=g.clearcoat>0,K=g.dispersion>0,T=g.iridescence>0,G=g.sheen>0,L=g.transmission>0,C=k&&!!g.anisotropyMap,z=Y&&!!g.clearcoatMap,O=Y&&!!g.clearcoatNormalMap,W=Y&&!!g.clearcoatRoughnessMap,Q=T&&!!g.iridescenceMap,xe=T&&!!g.iridescenceThicknessMap,fe=G&&!!g.sheenColorMap,Pe=G&&!!g.sheenRoughnessMap,Be=!!g.specularMap,he=!!g.specularColorMap,me=!!g.specularIntensityMap,M=L&&!!g.transmissionMap,ge=L&&!!g.thicknessMap,ee=!!g.gradientMap,De=!!g.alphaMap,Ce=g.alphaTest>0,Ke=!!g.alphaHash,Je=!!g.extensions,je=pi;g.toneMapped&&(Se!==null&&Se.isXRRenderTarget!==!0||(je=r.toneMapping));let Ne={shaderID:se,shaderType:g.type,shaderName:g.name,vertexShader:ne,fragmentShader:j,defines:g.defines,customVertexShaderID:J,customFragmentShaderID:ie,isRawShaderMaterial:g.isRawShaderMaterial===!0,glslVersion:g.glslVersion,precision:f,batching:Ae,batchingColor:Ae&&S._colorsTexture!==null,instancing:ze,instancingColor:ze&&S.instanceColor!==null,instancingMorph:ze&&S.morphTexture!==null,outputColorSpace:Se===null?r.outputColorSpace:Se.isXRRenderTarget===!0?Se.texture.colorSpace:Rr,alphaToCoverage:!!g.alphaToCoverage,map:Ie,matcap:ve,envMap:Ee,envMapMode:Ee&&X.mapping,envMapCubeUVHeight:H,aoMap:Le,lightMap:Ye,bumpMap:We,normalMap:U,displacementMap:D,emissiveMap:$,normalMapObjectSpace:U&&g.normalMapType===Gp,normalMapTangentSpace:U&&g.normalMapType===Hp,metalnessMap:B,roughnessMap:w,anisotropy:k,anisotropyMap:C,clearcoat:Y,clearcoatMap:z,clearcoatNormalMap:O,clearcoatRoughnessMap:W,dispersion:K,iridescence:T,iridescenceMap:Q,iridescenceThicknessMap:xe,sheen:G,sheenColorMap:fe,sheenRoughnessMap:Pe,specularMap:Be,specularColorMap:he,specularIntensityMap:me,transmission:L,transmissionMap:M,thicknessMap:ge,gradientMap:ee,opaque:g.transparent===!1&&g.blending===Ya&&g.alphaToCoverage===!1,alphaMap:De,alphaTest:Ce,alphaHash:Ke,combine:g.combine,mapUv:Ie&&v(g.map.channel),aoMapUv:Le&&v(g.aoMap.channel),lightMapUv:Ye&&v(g.lightMap.channel),bumpMapUv:We&&v(g.bumpMap.channel),normalMapUv:U&&v(g.normalMap.channel),displacementMapUv:D&&v(g.displacementMap.channel),emissiveMapUv:$&&v(g.emissiveMap.channel),metalnessMapUv:B&&v(g.metalnessMap.channel),roughnessMapUv:w&&v(g.roughnessMap.channel),anisotropyMapUv:C&&v(g.anisotropyMap.channel),clearcoatMapUv:z&&v(g.clearcoatMap.channel),clearcoatNormalMapUv:O&&v(g.clearcoatNormalMap.channel),clearcoatRoughnessMapUv:W&&v(g.clearcoatRoughnessMap.channel),iridescenceMapUv:Q&&v(g.iridescenceMap.channel),iridescenceThicknessMapUv:xe&&v(g.iridescenceThicknessMap.channel),sheenColorMapUv:fe&&v(g.sheenColorMap.channel),sheenRoughnessMapUv:Pe&&v(g.sheenRoughnessMap.channel),specularMapUv:Be&&v(g.specularMap.channel),specularColorMapUv:he&&v(g.specularColorMap.channel),specularIntensityMapUv:me&&v(g.specularIntensityMap.channel),transmissionMapUv:M&&v(g.transmissionMap.channel),thicknessMapUv:ge&&v(g.thicknessMap.channel),alphaMapUv:De&&v(g.alphaMap.channel),vertexTangents:!!R.attributes.tangent&&(U||k),vertexColors:g.vertexColors,vertexAlphas:g.vertexColors===!0&&!!R.attributes.color&&R.attributes.color.itemSize===4,pointsUvs:S.isPoints===!0&&!!R.attributes.uv&&(Ie||De),fog:!!E,useFog:g.fog===!0,fogExp2:!!E&&E.isFogExp2,flatShading:g.wireframe===!1&&(g.flatShading===!0||R.attributes.normal===void 0&&U===!1&&(g.isMeshLambertMaterial||g.isMeshPhongMaterial||g.isMeshStandardMaterial||g.isMeshPhysicalMaterial)),sizeAttenuation:g.sizeAttenuation===!0,logarithmicDepthBuffer:p,reversedDepthBuffer:we,skinning:S.isSkinnedMesh===!0,morphTargets:R.morphAttributes.position!==void 0,morphNormals:R.morphAttributes.normal!==void 0,morphColors:R.morphAttributes.color!==void 0,morphTargetsCount:te,morphTextureStride:re,numDirLights:u.directional.length,numPointLights:u.point.length,numSpotLights:u.spot.length,numSpotLightMaps:u.spotLightMap.length,numRectAreaLights:u.rectArea.length,numHemiLights:u.hemi.length,numDirLightShadows:u.directionalShadowMap.length,numPointLightShadows:u.pointShadowMap.length,numSpotLightShadows:u.spotShadowMap.length,numSpotLightShadowsWithMaps:u.numSpotLightShadowsWithMaps,numLightProbes:u.numLightProbes,numClippingPlanes:s.numPlanes,numClipIntersection:s.numIntersection,dithering:g.dithering,shadowMapEnabled:r.shadowMap.enabled&&y.length>0,shadowMapType:r.shadowMap.type,toneMapping:je,decodeVideoTexture:Ie&&g.map.isVideoTexture===!0&&Mt.getTransfer(g.map.colorSpace)===bt,decodeVideoTextureEmissive:$&&g.emissiveMap.isVideoTexture===!0&&Mt.getTransfer(g.emissiveMap.colorSpace)===bt,premultipliedAlpha:g.premultipliedAlpha,doubleSided:g.side===En,flipSided:g.side===In,useDepthPacking:g.depthPacking>=0,depthPacking:g.depthPacking||0,index0AttributeName:g.index0AttributeName,extensionClipCullDistance:Je&&g.extensions.clipCullDistance===!0&&t.has("WEBGL_clip_cull_distance"),extensionMultiDraw:(Je&&g.extensions.multiDraw===!0||Ae)&&t.has("WEBGL_multi_draw"),rendererExtensionParallelShaderCompile:t.has("KHR_parallel_shader_compile"),customProgramCacheKey:g.customProgramCacheKey()};return Ne.vertexUv1s=c.has(1),Ne.vertexUv2s=c.has(2),Ne.vertexUv3s=c.has(3),c.clear(),Ne},getProgramCacheKey:function(g){let u=[];if(g.shaderID?u.push(g.shaderID):(u.push(g.customVertexShaderID),u.push(g.customFragmentShaderID)),g.defines!==void 0)for(let y in g.defines)u.push(y),u.push(g.defines[y]);return g.isRawShaderMaterial===!1&&(function(y,x){y.push(x.precision),y.push(x.outputColorSpace),y.push(x.envMapMode),y.push(x.envMapCubeUVHeight),y.push(x.mapUv),y.push(x.alphaMapUv),y.push(x.lightMapUv),y.push(x.aoMapUv),y.push(x.bumpMapUv),y.push(x.normalMapUv),y.push(x.displacementMapUv),y.push(x.emissiveMapUv),y.push(x.metalnessMapUv),y.push(x.roughnessMapUv),y.push(x.anisotropyMapUv),y.push(x.clearcoatMapUv),y.push(x.clearcoatNormalMapUv),y.push(x.clearcoatRoughnessMapUv),y.push(x.iridescenceMapUv),y.push(x.iridescenceThicknessMapUv),y.push(x.sheenColorMapUv),y.push(x.sheenRoughnessMapUv),y.push(x.specularMapUv),y.push(x.specularColorMapUv),y.push(x.specularIntensityMapUv),y.push(x.transmissionMapUv),y.push(x.thicknessMapUv),y.push(x.combine),y.push(x.fogExp2),y.push(x.sizeAttenuation),y.push(x.morphTargetsCount),y.push(x.morphAttributeCount),y.push(x.numDirLights),y.push(x.numPointLights),y.push(x.numSpotLights),y.push(x.numSpotLightMaps),y.push(x.numHemiLights),y.push(x.numRectAreaLights),y.push(x.numDirLightShadows),y.push(x.numPointLightShadows),y.push(x.numSpotLightShadows),y.push(x.numSpotLightShadowsWithMaps),y.push(x.numLightProbes),y.push(x.shadowMapType),y.push(x.toneMapping),y.push(x.numClippingPlanes),y.push(x.numClipIntersection),y.push(x.depthPacking)}(u,g),function(y,x){a.disableAll(),x.instancing&&a.enable(0),x.instancingColor&&a.enable(1),x.instancingMorph&&a.enable(2),x.matcap&&a.enable(3),x.envMap&&a.enable(4),x.normalMapObjectSpace&&a.enable(5),x.normalMapTangentSpace&&a.enable(6),x.clearcoat&&a.enable(7),x.iridescence&&a.enable(8),x.alphaTest&&a.enable(9),x.vertexColors&&a.enable(10),x.vertexAlphas&&a.enable(11),x.vertexUv1s&&a.enable(12),x.vertexUv2s&&a.enable(13),x.vertexUv3s&&a.enable(14),x.vertexTangents&&a.enable(15),x.anisotropy&&a.enable(16),x.alphaHash&&a.enable(17),x.batching&&a.enable(18),x.dispersion&&a.enable(19),x.batchingColor&&a.enable(20),x.gradientMap&&a.enable(21),y.push(a.mask),a.disableAll(),x.fog&&a.enable(0),x.useFog&&a.enable(1),x.flatShading&&a.enable(2),x.logarithmicDepthBuffer&&a.enable(3),x.reversedDepthBuffer&&a.enable(4),x.skinning&&a.enable(5),x.morphTargets&&a.enable(6),x.morphNormals&&a.enable(7),x.morphColors&&a.enable(8),x.premultipliedAlpha&&a.enable(9),x.shadowMapEnabled&&a.enable(10),x.doubleSided&&a.enable(11),x.flipSided&&a.enable(12),x.useDepthPacking&&a.enable(13),x.dithering&&a.enable(14),x.transmission&&a.enable(15),x.sheen&&a.enable(16),x.opaque&&a.enable(17),x.pointsUvs&&a.enable(18),x.decodeVideoTexture&&a.enable(19),x.decodeVideoTextureEmissive&&a.enable(20),x.alphaToCoverage&&a.enable(21),y.push(a.mask)}(u,g),u.push(r.outputColorSpace)),u.push(g.customProgramCacheKey),u.join()},getUniforms:function(g){let u=m[g.type],y;if(u){let x=Ci[u];y=rf.clone(x.uniforms)}else y=g.uniforms;return y},acquireProgram:function(g,u){let y=h.get(u);return y!==void 0?++y.usedTimes:(y=new ag(r,u,g,i),l.push(y),h.set(u,y)),y},releaseProgram:function(g){if(--g.usedTimes===0){let u=l.indexOf(g);l[u]=l[l.length-1],l.pop(),h.delete(g.cacheKey),g.destroy()}},releaseShaderCache:function(g){o.remove(g)},programs:l,dispose:function(){o.dispose()}}}function cg(){let r=new WeakMap;return{has:function(e){return r.has(e)},get:function(e){let t=r.get(e);return t===void 0&&(t={},r.set(e,t)),t},remove:function(e){r.delete(e)},update:function(e,t,n){r.get(e)[t]=n},dispose:function(){r=new WeakMap}}}function hg(r,e){return r.groupOrder!==e.groupOrder?r.groupOrder-e.groupOrder:r.renderOrder!==e.renderOrder?r.renderOrder-e.renderOrder:r.material.id!==e.material.id?r.material.id-e.material.id:r.materialVariant!==e.materialVariant?r.materialVariant-e.materialVariant:r.z!==e.z?r.z-e.z:r.id-e.id}function Tf(r,e){return r.groupOrder!==e.groupOrder?r.groupOrder-e.groupOrder:r.renderOrder!==e.renderOrder?r.renderOrder-e.renderOrder:r.z!==e.z?e.z-r.z:r.id-e.id}function Ef(){let r=[],e=0,t=[],n=[],i=[];function s(o){let c=0;return o.isInstancedMesh&&(c+=2),o.isSkinnedMesh&&(c+=1),c}function a(o,c,l,h,p,f){let m=r[e];return m===void 0?(m={id:o.id,object:o,geometry:c,material:l,materialVariant:s(o),groupOrder:h,renderOrder:o.renderOrder,z:p,group:f},r[e]=m):(m.id=o.id,m.object=o,m.geometry=c,m.material=l,m.materialVariant=s(o),m.groupOrder=h,m.renderOrder=o.renderOrder,m.z=p,m.group=f),e++,m}return{opaque:t,transmissive:n,transparent:i,init:function(){e=0,t.length=0,n.length=0,i.length=0},push:function(o,c,l,h,p,f){let m=a(o,c,l,h,p,f);l.transmission>0?n.push(m):l.transparent===!0?i.push(m):t.push(m)},unshift:function(o,c,l,h,p,f){let m=a(o,c,l,h,p,f);l.transmission>0?n.unshift(m):l.transparent===!0?i.unshift(m):t.unshift(m)},finish:function(){for(let o=e,c=r.length;o<c;o++){let l=r[o];if(l.id===null)break;l.id=null,l.object=null,l.geometry=null,l.material=null,l.group=null}},sort:function(o,c){t.length>1&&t.sort(o||hg),n.length>1&&n.sort(c||Tf),i.length>1&&i.sort(c||Tf)}}}function ug(){let r=new WeakMap;return{get:function(e,t){let n=r.get(e),i;return n===void 0?(i=new Ef,r.set(e,[i])):t>=n.length?(i=new Ef,n.push(i)):i=n[t],i},dispose:function(){r=new WeakMap}}}function dg(){let r={};return{get:function(e){if(r[e.id]!==void 0)return r[e.id];let t;switch(e.type){case"DirectionalLight":t={direction:new N,color:new ke};break;case"SpotLight":t={position:new N,direction:new N,color:new ke,distance:0,coneCos:0,penumbraCos:0,decay:0};break;case"PointLight":t={position:new N,color:new ke,distance:0,decay:0};break;case"HemisphereLight":t={direction:new N,skyColor:new ke,groundColor:new ke};break;case"RectAreaLight":t={color:new ke,position:new N,halfWidth:new N,halfHeight:new N}}return r[e.id]=t,t}}}var pg=0;function fg(r,e){return(e.castShadow?2:0)-(r.castShadow?2:0)+(e.map?1:0)-(r.map?1:0)}function mg(r){let e=new dg,t=function(){let o={};return{get:function(c){if(o[c.id]!==void 0)return o[c.id];let l;switch(c.type){case"DirectionalLight":case"SpotLight":l={shadowIntensity:1,shadowBias:0,shadowNormalBias:0,shadowRadius:1,shadowMapSize:new Te};break;case"PointLight":l={shadowIntensity:1,shadowBias:0,shadowNormalBias:0,shadowRadius:1,shadowMapSize:new Te,shadowCameraNear:1,shadowCameraFar:1e3}}return o[c.id]=l,l}}}(),n={version:0,hash:{directionalLength:-1,pointLength:-1,spotLength:-1,rectAreaLength:-1,hemiLength:-1,numDirectionalShadows:-1,numPointShadows:-1,numSpotShadows:-1,numSpotMaps:-1,numLightProbes:-1},ambient:[0,0,0],probe:[],directional:[],directionalShadow:[],directionalShadowMap:[],directionalShadowMatrix:[],spot:[],spotLightMap:[],spotShadow:[],spotShadowMap:[],spotLightMatrix:[],rectArea:[],rectAreaLTC1:null,rectAreaLTC2:null,point:[],pointShadow:[],pointShadowMap:[],pointShadowMatrix:[],hemi:[],numSpotLightShadowsWithMaps:0,numLightProbes:0};for(let o=0;o<9;o++)n.probe.push(new N);let i=new N,s=new st,a=new st;return{setup:function(o){let c=0,l=0,h=0;for(let V=0;V<9;V++)n.probe[V].set(0,0,0);let p=0,f=0,m=0,v=0,g=0,u=0,y=0,x=0,S=0,E=0,R=0;o.sort(fg);for(let V=0,X=o.length;V<X;V++){let H=o[V],se=H.color,oe=H.intensity,te=H.distance,ne=null;if(H.shadow&&H.shadow.map&&(ne=H.shadow.map.texture.format===ks?H.shadow.map.texture:H.shadow.map.depthTexture||H.shadow.map.texture),H.isAmbientLight)c+=se.r*oe,l+=se.g*oe,h+=se.b*oe;else if(H.isLightProbe){for(let j=0;j<9;j++)n.probe[j].addScaledVector(H.sh.coefficients[j],oe);R++}else if(H.isDirectionalLight){let j=e.get(H);if(j.color.copy(H.color).multiplyScalar(H.intensity),H.castShadow){let J=H.shadow,ie=t.get(H);ie.shadowIntensity=J.intensity,ie.shadowBias=J.bias,ie.shadowNormalBias=J.normalBias,ie.shadowRadius=J.radius,ie.shadowMapSize=J.mapSize,n.directionalShadow[p]=ie,n.directionalShadowMap[p]=ne,n.directionalShadowMatrix[p]=H.shadow.matrix,u++}n.directional[p]=j,p++}else if(H.isSpotLight){let j=e.get(H);j.position.setFromMatrixPosition(H.matrixWorld),j.color.copy(se).multiplyScalar(oe),j.distance=te,j.coneCos=Math.cos(H.angle),j.penumbraCos=Math.cos(H.angle*(1-H.penumbra)),j.decay=H.decay,n.spot[m]=j;let J=H.shadow;if(H.map&&(n.spotLightMap[S]=H.map,S++,J.updateMatrices(H),H.castShadow&&E++),n.spotLightMatrix[m]=J.matrix,H.castShadow){let ie=t.get(H);ie.shadowIntensity=J.intensity,ie.shadowBias=J.bias,ie.shadowNormalBias=J.normalBias,ie.shadowRadius=J.radius,ie.shadowMapSize=J.mapSize,n.spotShadow[m]=ie,n.spotShadowMap[m]=ne,x++}m++}else if(H.isRectAreaLight){let j=e.get(H);j.color.copy(se).multiplyScalar(oe),j.halfWidth.set(.5*H.width,0,0),j.halfHeight.set(0,.5*H.height,0),n.rectArea[v]=j,v++}else if(H.isPointLight){let j=e.get(H);if(j.color.copy(H.color).multiplyScalar(H.intensity),j.distance=H.distance,j.decay=H.decay,H.castShadow){let J=H.shadow,ie=t.get(H);ie.shadowIntensity=J.intensity,ie.shadowBias=J.bias,ie.shadowNormalBias=J.normalBias,ie.shadowRadius=J.radius,ie.shadowMapSize=J.mapSize,ie.shadowCameraNear=J.camera.near,ie.shadowCameraFar=J.camera.far,n.pointShadow[f]=ie,n.pointShadowMap[f]=ne,n.pointShadowMatrix[f]=H.shadow.matrix,y++}n.point[f]=j,f++}else if(H.isHemisphereLight){let j=e.get(H);j.skyColor.copy(H.color).multiplyScalar(oe),j.groundColor.copy(H.groundColor).multiplyScalar(oe),n.hemi[g]=j,g++}}v>0&&(r.has("OES_texture_float_linear")===!0?(n.rectAreaLTC1=Ge.LTC_FLOAT_1,n.rectAreaLTC2=Ge.LTC_FLOAT_2):(n.rectAreaLTC1=Ge.LTC_HALF_1,n.rectAreaLTC2=Ge.LTC_HALF_2)),n.ambient[0]=c,n.ambient[1]=l,n.ambient[2]=h;let P=n.hash;P.directionalLength===p&&P.pointLength===f&&P.spotLength===m&&P.rectAreaLength===v&&P.hemiLength===g&&P.numDirectionalShadows===u&&P.numPointShadows===y&&P.numSpotShadows===x&&P.numSpotMaps===S&&P.numLightProbes===R||(n.directional.length=p,n.spot.length=m,n.rectArea.length=v,n.point.length=f,n.hemi.length=g,n.directionalShadow.length=u,n.directionalShadowMap.length=u,n.pointShadow.length=y,n.pointShadowMap.length=y,n.spotShadow.length=x,n.spotShadowMap.length=x,n.directionalShadowMatrix.length=u,n.pointShadowMatrix.length=y,n.spotLightMatrix.length=x+S-E,n.spotLightMap.length=S,n.numSpotLightShadowsWithMaps=E,n.numLightProbes=R,P.directionalLength=p,P.pointLength=f,P.spotLength=m,P.rectAreaLength=v,P.hemiLength=g,P.numDirectionalShadows=u,P.numPointShadows=y,P.numSpotShadows=x,P.numSpotMaps=S,P.numLightProbes=R,n.version=pg++)},setupView:function(o,c){let l=0,h=0,p=0,f=0,m=0,v=c.matrixWorldInverse;for(let g=0,u=o.length;g<u;g++){let y=o[g];if(y.isDirectionalLight){let x=n.directional[l];x.direction.setFromMatrixPosition(y.matrixWorld),i.setFromMatrixPosition(y.target.matrixWorld),x.direction.sub(i),x.direction.transformDirection(v),l++}else if(y.isSpotLight){let x=n.spot[p];x.position.setFromMatrixPosition(y.matrixWorld),x.position.applyMatrix4(v),x.direction.setFromMatrixPosition(y.matrixWorld),i.setFromMatrixPosition(y.target.matrixWorld),x.direction.sub(i),x.direction.transformDirection(v),p++}else if(y.isRectAreaLight){let x=n.rectArea[f];x.position.setFromMatrixPosition(y.matrixWorld),x.position.applyMatrix4(v),a.identity(),s.copy(y.matrixWorld),s.premultiply(v),a.extractRotation(s),x.halfWidth.set(.5*y.width,0,0),x.halfHeight.set(0,.5*y.height,0),x.halfWidth.applyMatrix4(a),x.halfHeight.applyMatrix4(a),f++}else if(y.isPointLight){let x=n.point[h];x.position.setFromMatrixPosition(y.matrixWorld),x.position.applyMatrix4(v),h++}else if(y.isHemisphereLight){let x=n.hemi[m];x.direction.setFromMatrixPosition(y.matrixWorld),x.direction.transformDirection(v),m++}}},state:n}}function wf(r){let e=new mg(r),t=[],n=[],i={lightsArray:t,shadowsArray:n,camera:null,lights:e,transmissionRenderTarget:{}};return{init:function(s){i.camera=s,t.length=0,n.length=0},state:i,setupLights:function(){e.setup(t)},setupLightsView:function(s){e.setupView(t,s)},pushLight:function(s){t.push(s)},pushShadow:function(s){n.push(s)}}}function gg(r){let e=new WeakMap;return{get:function(t,n=0){let i=e.get(t),s;return i===void 0?(s=new wf(r),e.set(t,[s])):n>=i.length?(s=new wf(r),i.push(s)):s=i[n],s},dispose:function(){e=new WeakMap}}}var _g=[new N(1,0,0),new N(-1,0,0),new N(0,1,0),new N(0,-1,0),new N(0,0,1),new N(0,0,-1)],vg=[new N(0,-1,0),new N(0,-1,0),new N(0,0,1),new N(0,0,-1),new N(0,-1,0),new N(0,-1,0)],Af=new st,no=new N,Wu=new N;function yg(r,e,t){let n=new or,i=new Te,s=new Te,a=new Lt,o=new Ol,c=new Fl,l={},h=t.maxTextureSize,p={[Ns]:In,[In]:Ns,[En]:En},f=new qn({defines:{VSM_SAMPLES:8},uniforms:{shadow_pass:{value:null},resolution:{value:new Te},radius:{value:4}},vertexShader:`void main() {
	gl_Position = vec4( position, 1.0 );
}`,fragmentShader:`uniform sampler2D shadow_pass;
uniform vec2 resolution;
uniform float radius;
void main() {
	const float samples = float( VSM_SAMPLES );
	float mean = 0.0;
	float squared_mean = 0.0;
	float uvStride = samples <= 1.0 ? 0.0 : 2.0 / ( samples - 1.0 );
	float uvStart = samples <= 1.0 ? 0.0 : - 1.0;
	for ( float i = 0.0; i < samples; i ++ ) {
		float uvOffset = uvStart + i * uvStride;
		#ifdef HORIZONTAL_PASS
			vec2 distribution = texture2D( shadow_pass, ( gl_FragCoord.xy + vec2( uvOffset, 0.0 ) * radius ) / resolution ).rg;
			mean += distribution.x;
			squared_mean += distribution.y * distribution.y + distribution.x * distribution.x;
		#else
			float depth = texture2D( shadow_pass, ( gl_FragCoord.xy + vec2( 0.0, uvOffset ) * radius ) / resolution ).r;
			mean += depth;
			squared_mean += depth * depth;
		#endif
	}
	mean = mean / samples;
	squared_mean = squared_mean / samples;
	float std_dev = sqrt( max( 0.0, squared_mean - mean * mean ) );
	gl_FragColor = vec4( mean, std_dev, 0.0, 1.0 );
}`}),m=f.clone();m.defines.HORIZONTAL_PASS=1;let v=new St;v.setAttribute("position",new on(new Float32Array([-1,-1,.5,3,-1,.5,-1,3,.5]),3));let g=new Et(v,f),u=this;this.enabled=!1,this.autoUpdate=!0,this.needsUpdate=!1,this.type=Dr;let y=this.type;function x(P,V){let X=e.update(g);f.defines.VSM_SAMPLES!==P.blurSamples&&(f.defines.VSM_SAMPLES=P.blurSamples,m.defines.VSM_SAMPLES=P.blurSamples,f.needsUpdate=!0,m.needsUpdate=!0),P.mapPass===null&&(P.mapPass=new Vn(i.x,i.y,{format:ks,type:wi})),f.uniforms.shadow_pass.value=P.map.depthTexture,f.uniforms.resolution.value=P.mapSize,f.uniforms.radius.value=P.radius,r.setRenderTarget(P.mapPass),r.clear(),r.renderBufferDirect(V,null,X,f,g,null),m.uniforms.shadow_pass.value=P.mapPass.texture,m.uniforms.resolution.value=P.mapSize,m.uniforms.radius.value=P.radius,r.setRenderTarget(P.map),r.clear(),r.renderBufferDirect(V,null,X,m,g,null)}function S(P,V,X,H){let se=null,oe=X.isPointLight===!0?P.customDistanceMaterial:P.customDepthMaterial;if(oe!==void 0)se=oe;else if(se=X.isPointLight===!0?c:o,r.localClippingEnabled&&V.clipShadows===!0&&Array.isArray(V.clippingPlanes)&&V.clippingPlanes.length!==0||V.displacementMap&&V.displacementScale!==0||V.alphaMap&&V.alphaTest>0||V.map&&V.alphaTest>0||V.alphaToCoverage===!0){let te=se.uuid,ne=V.uuid,j=l[te];j===void 0&&(j={},l[te]=j);let J=j[ne];J===void 0&&(J=se.clone(),j[ne]=J,V.addEventListener("dispose",R)),se=J}return se.visible=V.visible,se.wireframe=V.wireframe,se.side=H===Us?V.shadowSide!==null?V.shadowSide:V.side:V.shadowSide!==null?V.shadowSide:p[V.side],se.alphaMap=V.alphaMap,se.alphaTest=V.alphaToCoverage===!0?.5:V.alphaTest,se.map=V.map,se.clipShadows=V.clipShadows,se.clippingPlanes=V.clippingPlanes,se.clipIntersection=V.clipIntersection,se.displacementMap=V.displacementMap,se.displacementScale=V.displacementScale,se.displacementBias=V.displacementBias,se.wireframeLinewidth=V.wireframeLinewidth,se.linewidth=V.linewidth,X.isPointLight===!0&&se.isMeshDistanceMaterial===!0&&(r.properties.get(se).light=X),se}function E(P,V,X,H,se){if(P.visible===!1)return;if(P.layers.test(V.layers)&&(P.isMesh||P.isLine||P.isPoints)&&(P.castShadow||P.receiveShadow&&se===Us)&&(!P.frustumCulled||n.intersectsObject(P))){P.modelViewMatrix.multiplyMatrices(X.matrixWorldInverse,P.matrixWorld);let te=e.update(P),ne=P.material;if(Array.isArray(ne)){let j=te.groups;for(let J=0,ie=j.length;J<ie;J++){let re=j[J],Se=ne[re.materialIndex];if(Se&&Se.visible){let we=S(P,Se,H,se);P.onBeforeShadow(r,P,V,X,te,we,re),r.renderBufferDirect(X,null,te,we,P,re),P.onAfterShadow(r,P,V,X,te,we,re)}}}else if(ne.visible){let j=S(P,ne,H,se);P.onBeforeShadow(r,P,V,X,te,j,null),r.renderBufferDirect(X,null,te,j,P,null),P.onAfterShadow(r,P,V,X,te,j,null)}}let oe=P.children;for(let te=0,ne=oe.length;te<ne;te++)E(oe[te],V,X,H,se)}function R(P){P.target.removeEventListener("dispose",R);for(let V in l){let X=l[V],H=P.target.uuid;H in X&&(X[H].dispose(),delete X[H])}}this.render=function(P,V,X){if(u.enabled===!1||u.autoUpdate===!1&&u.needsUpdate===!1||P.length===0)return;this.type===fp&&(tt("WebGLShadowMap: PCFSoftShadowMap has been deprecated. Using PCFShadowMap instead."),this.type=Dr);let H=r.getRenderTarget(),se=r.getActiveCubeFace(),oe=r.getActiveMipmapLevel(),te=r.state;te.setBlending(Ei),te.buffers.depth.getReversed()===!0?te.buffers.color.setClear(0,0,0,0):te.buffers.color.setClear(1,1,1,1),te.buffers.depth.setTest(!0),te.setScissorTest(!1);let ne=y!==this.type;ne&&V.traverse(function(j){j.material&&(Array.isArray(j.material)?j.material.forEach(J=>J.needsUpdate=!0):j.material.needsUpdate=!0)});for(let j=0,J=P.length;j<J;j++){let ie=P[j],re=ie.shadow;if(re===void 0){tt("WebGLShadowMap:",ie,"has no shadow.");continue}if(re.autoUpdate===!1&&re.needsUpdate===!1)continue;i.copy(re.mapSize);let Se=re.getFrameExtents();i.multiply(Se),s.copy(re.mapSize),(i.x>h||i.y>h)&&(i.x>h&&(s.x=Math.floor(h/Se.x),i.x=s.x*Se.x,re.mapSize.x=s.x),i.y>h&&(s.y=Math.floor(h/Se.y),i.y=s.y*Se.y,re.mapSize.y=s.y));let we=r.state.buffers.depth.getReversed();if(re.camera._reversedDepth=we,re.map===null||ne===!0){if(re.map!==null&&(re.map.depthTexture!==null&&(re.map.depthTexture.dispose(),re.map.depthTexture=null),re.map.dispose()),this.type===Us){if(ie.isPointLight){tt("WebGLShadowMap: VSM shadow maps are not supported for PointLights. Use PCF or BasicShadowMap instead.");continue}re.map=new Vn(i.x,i.y,{format:ks,type:wi,minFilter:nn,magFilter:nn,generateMipmaps:!1}),re.map.texture.name=ie.name+".shadowMap",re.map.depthTexture=new lr(i.x,i.y,si),re.map.depthTexture.name=ie.name+".shadowMapDepth",re.map.depthTexture.format=pr,re.map.depthTexture.compareFunction=null,re.map.depthTexture.minFilter=ni,re.map.depthTexture.magFilter=ni}else ie.isPointLight?(re.map=new _c(i.x),re.map.depthTexture=new dl(i.x,qi)):(re.map=new Vn(i.x,i.y),re.map.depthTexture=new lr(i.x,i.y,qi)),re.map.depthTexture.name=ie.name+".shadowMap",re.map.depthTexture.format=pr,this.type===Dr?(re.map.depthTexture.compareFunction=we?uc:hc,re.map.depthTexture.minFilter=nn,re.map.depthTexture.magFilter=nn):(re.map.depthTexture.compareFunction=null,re.map.depthTexture.minFilter=ni,re.map.depthTexture.magFilter=ni);re.camera.updateProjectionMatrix()}let ze=re.map.isWebGLCubeRenderTarget?6:1;for(let Ae=0;Ae<ze;Ae++){if(re.map.isWebGLCubeRenderTarget)r.setRenderTarget(re.map,Ae),r.clear();else{Ae===0&&(r.setRenderTarget(re.map),r.clear());let Ie=re.getViewport(Ae);a.set(s.x*Ie.x,s.y*Ie.y,s.x*Ie.z,s.y*Ie.w),te.viewport(a)}if(ie.isPointLight){let Ie=re.camera,ve=re.matrix,Ee=ie.distance||Ie.far;Ee!==Ie.far&&(Ie.far=Ee,Ie.updateProjectionMatrix()),no.setFromMatrixPosition(ie.matrixWorld),Ie.position.copy(no),Wu.copy(Ie.position),Wu.add(_g[Ae]),Ie.up.copy(vg[Ae]),Ie.lookAt(Wu),Ie.updateMatrixWorld(),ve.makeTranslation(-no.x,-no.y,-no.z),Af.multiplyMatrices(Ie.projectionMatrix,Ie.matrixWorldInverse),re._frustum.setFromProjectionMatrix(Af,Ie.coordinateSystem,Ie.reversedDepth)}else re.updateMatrices(ie);n=re.getFrustum(),E(V,X,re.camera,ie,this.type)}re.isPointLightShadow!==!0&&this.type===Us&&x(re,X),re.needsUpdate=!1}y=this.type,u.needsUpdate=!1,r.setRenderTarget(H,se,oe)}}function xg(r,e){let t=new function(){let w=!1,k=new Lt,Y=null,K=new Lt(0,0,0,0);return{setMask:function(T){Y===T||w||(r.colorMask(T,T,T,T),Y=T)},setLocked:function(T){w=T},setClear:function(T,G,L,C,z){z===!0&&(T*=C,G*=C,L*=C),k.set(T,G,L,C),K.equals(k)===!1&&(r.clearColor(T,G,L,C),K.copy(k))},reset:function(){w=!1,Y=null,K.set(-1,0,0,0)}}},n=new function(){let w=!1,k=!1,Y=null,K=null,T=null;return{setReversed:function(G){if(k!==G){let L=e.get("EXT_clip_control");G?L.clipControlEXT(L.LOWER_LEFT_EXT,L.ZERO_TO_ONE_EXT):L.clipControlEXT(L.LOWER_LEFT_EXT,L.NEGATIVE_ONE_TO_ONE_EXT),k=G;let C=T;T=null,this.setClear(C)}},getReversed:function(){return k},setTest:function(G){G?Ee(r.DEPTH_TEST):Le(r.DEPTH_TEST)},setMask:function(G){Y===G||w||(r.depthMask(G),Y=G)},setFunc:function(G){if(k&&(G=$p[G]),K!==G){switch(G){case Nh:r.depthFunc(r.NEVER);break;case Oh:r.depthFunc(r.ALWAYS);break;case Fh:r.depthFunc(r.LESS);break;case Kl:r.depthFunc(r.LEQUAL);break;case Bh:r.depthFunc(r.EQUAL);break;case zh:r.depthFunc(r.GEQUAL);break;case kh:r.depthFunc(r.GREATER);break;case Hh:r.depthFunc(r.NOTEQUAL);break;default:r.depthFunc(r.LEQUAL)}K=G}},setLocked:function(G){w=G},setClear:function(G){T!==G&&(T=G,k&&(G=1-G),r.clearDepth(G))},reset:function(){w=!1,Y=null,K=null,T=null,k=!1}}},i=new function(){let w=!1,k=null,Y=null,K=null,T=null,G=null,L=null,C=null,z=null;return{setTest:function(O){w||(O?Ee(r.STENCIL_TEST):Le(r.STENCIL_TEST))},setMask:function(O){k===O||w||(r.stencilMask(O),k=O)},setFunc:function(O,W,Q){Y===O&&K===W&&T===Q||(r.stencilFunc(O,W,Q),Y=O,K=W,T=Q)},setOp:function(O,W,Q){G===O&&L===W&&C===Q||(r.stencilOp(O,W,Q),G=O,L=W,C=Q)},setLocked:function(O){w=O},setClear:function(O){z!==O&&(r.clearStencil(O),z=O)},reset:function(){w=!1,k=null,Y=null,K=null,T=null,G=null,L=null,C=null,z=null}}},s=new WeakMap,a=new WeakMap,o={},c={},l=new WeakMap,h=[],p=null,f=!1,m=null,v=null,g=null,u=null,y=null,x=null,S=null,E=new ke(0,0,0),R=0,P=!1,V=null,X=null,H=null,se=null,oe=null,te=r.getParameter(r.MAX_COMBINED_TEXTURE_IMAGE_UNITS),ne=!1,j=0,J=r.getParameter(r.VERSION);J.indexOf("WebGL")!==-1?(j=parseFloat(/^WebGL (\d)/.exec(J)[1]),ne=j>=1):J.indexOf("OpenGL ES")!==-1&&(j=parseFloat(/^OpenGL ES (\d)/.exec(J)[1]),ne=j>=2);let ie=null,re={},Se=r.getParameter(r.SCISSOR_BOX),we=r.getParameter(r.VIEWPORT),ze=new Lt().fromArray(Se),Ae=new Lt().fromArray(we);function Ie(w,k,Y,K){let T=new Uint8Array(4),G=r.createTexture();r.bindTexture(w,G),r.texParameteri(w,r.TEXTURE_MIN_FILTER,r.NEAREST),r.texParameteri(w,r.TEXTURE_MAG_FILTER,r.NEAREST);for(let L=0;L<Y;L++)w===r.TEXTURE_3D||w===r.TEXTURE_2D_ARRAY?r.texImage3D(k,0,r.RGBA,1,1,K,0,r.RGBA,r.UNSIGNED_BYTE,T):r.texImage2D(k+L,0,r.RGBA,1,1,0,r.RGBA,r.UNSIGNED_BYTE,T);return G}let ve={};function Ee(w){o[w]!==!0&&(r.enable(w),o[w]=!0)}function Le(w){o[w]!==!1&&(r.disable(w),o[w]=!1)}ve[r.TEXTURE_2D]=Ie(r.TEXTURE_2D,r.TEXTURE_2D,1),ve[r.TEXTURE_CUBE_MAP]=Ie(r.TEXTURE_CUBE_MAP,r.TEXTURE_CUBE_MAP_POSITIVE_X,6),ve[r.TEXTURE_2D_ARRAY]=Ie(r.TEXTURE_2D_ARRAY,r.TEXTURE_2D_ARRAY,1,1),ve[r.TEXTURE_3D]=Ie(r.TEXTURE_3D,r.TEXTURE_3D,1,1),t.setClear(0,0,0,1),n.setClear(1),i.setClear(0),Ee(r.DEPTH_TEST),n.setFunc(Kl),D(!1),$(Lh),Ee(r.CULL_FACE),U(Ei);let Ye={[Os]:r.FUNC_ADD,[gp]:r.FUNC_SUBTRACT,[_p]:r.FUNC_REVERSE_SUBTRACT};Ye[vp]=r.MIN,Ye[yp]=r.MAX;let We={[xp]:r.ZERO,[Mp]:r.ONE,[bp]:r.SRC_COLOR,[Tp]:r.SRC_ALPHA,[Pp]:r.SRC_ALPHA_SATURATE,[Cp]:r.DST_COLOR,[wp]:r.DST_ALPHA,[Sp]:r.ONE_MINUS_SRC_COLOR,[Ep]:r.ONE_MINUS_SRC_ALPHA,[Rp]:r.ONE_MINUS_DST_COLOR,[Ap]:r.ONE_MINUS_DST_ALPHA,[Ip]:r.CONSTANT_COLOR,[Lp]:r.ONE_MINUS_CONSTANT_COLOR,[Dp]:r.CONSTANT_ALPHA,[Up]:r.ONE_MINUS_CONSTANT_ALPHA};function U(w,k,Y,K,T,G,L,C,z,O){if(w!==Ei){if(f===!1&&(Ee(r.BLEND),f=!0),w===mp)T=T||k,G=G||Y,L=L||K,k===v&&T===y||(r.blendEquationSeparate(Ye[k],Ye[T]),v=k,y=T),Y===g&&K===u&&G===x&&L===S||(r.blendFuncSeparate(We[Y],We[K],We[G],We[L]),g=Y,u=K,x=G,S=L),C.equals(E)!==!1&&z===R||(r.blendColor(C.r,C.g,C.b,z),E.copy(C),R=z),m=w,P=!1;else if(w!==m||O!==P){if(v===Os&&y===Os||(r.blendEquation(r.FUNC_ADD),v=Os,y=Os),O)switch(w){case Ya:r.blendFuncSeparate(r.ONE,r.ONE_MINUS_SRC_ALPHA,r.ONE,r.ONE_MINUS_SRC_ALPHA);break;case Za:r.blendFunc(r.ONE,r.ONE);break;case Dh:r.blendFuncSeparate(r.ZERO,r.ONE_MINUS_SRC_COLOR,r.ZERO,r.ONE);break;case Uh:r.blendFuncSeparate(r.DST_COLOR,r.ONE_MINUS_SRC_ALPHA,r.ZERO,r.ONE);break;default:rt("WebGLState: Invalid blending: ",w)}else switch(w){case Ya:r.blendFuncSeparate(r.SRC_ALPHA,r.ONE_MINUS_SRC_ALPHA,r.ONE,r.ONE_MINUS_SRC_ALPHA);break;case Za:r.blendFuncSeparate(r.SRC_ALPHA,r.ONE,r.ONE,r.ONE);break;case Dh:rt("WebGLState: SubtractiveBlending requires material.premultipliedAlpha = true");break;case Uh:rt("WebGLState: MultiplyBlending requires material.premultipliedAlpha = true");break;default:rt("WebGLState: Invalid blending: ",w)}g=null,u=null,x=null,S=null,E.set(0,0,0),R=0,m=w,P=O}}else f===!0&&(Le(r.BLEND),f=!1)}function D(w){V!==w&&(w?r.frontFace(r.CW):r.frontFace(r.CCW),V=w)}function $(w){w!==dp?(Ee(r.CULL_FACE),w!==X&&(w===Lh?r.cullFace(r.BACK):w===pp?r.cullFace(r.FRONT):r.cullFace(r.FRONT_AND_BACK))):Le(r.CULL_FACE),X=w}function B(w,k,Y){w?(Ee(r.POLYGON_OFFSET_FILL),se===k&&oe===Y||(se=k,oe=Y,n.getReversed()&&(k=-k),r.polygonOffset(k,Y))):Le(r.POLYGON_OFFSET_FILL)}return{buffers:{color:t,depth:n,stencil:i},enable:Ee,disable:Le,bindFramebuffer:function(w,k){return c[w]!==k&&(r.bindFramebuffer(w,k),c[w]=k,w===r.DRAW_FRAMEBUFFER&&(c[r.FRAMEBUFFER]=k),w===r.FRAMEBUFFER&&(c[r.DRAW_FRAMEBUFFER]=k),!0)},drawBuffers:function(w,k){let Y=h,K=!1;if(w){Y=l.get(k),Y===void 0&&(Y=[],l.set(k,Y));let T=w.textures;if(Y.length!==T.length||Y[0]!==r.COLOR_ATTACHMENT0){for(let G=0,L=T.length;G<L;G++)Y[G]=r.COLOR_ATTACHMENT0+G;Y.length=T.length,K=!0}}else Y[0]!==r.BACK&&(Y[0]=r.BACK,K=!0);K&&r.drawBuffers(Y)},useProgram:function(w){return p!==w&&(r.useProgram(w),p=w,!0)},setBlending:U,setMaterial:function(w,k){w.side===En?Le(r.CULL_FACE):Ee(r.CULL_FACE);let Y=w.side===In;k&&(Y=!Y),D(Y),w.blending===Ya&&w.transparent===!1?U(Ei):U(w.blending,w.blendEquation,w.blendSrc,w.blendDst,w.blendEquationAlpha,w.blendSrcAlpha,w.blendDstAlpha,w.blendColor,w.blendAlpha,w.premultipliedAlpha),n.setFunc(w.depthFunc),n.setTest(w.depthTest),n.setMask(w.depthWrite),t.setMask(w.colorWrite);let K=w.stencilWrite;i.setTest(K),K&&(i.setMask(w.stencilWriteMask),i.setFunc(w.stencilFunc,w.stencilRef,w.stencilFuncMask),i.setOp(w.stencilFail,w.stencilZFail,w.stencilZPass)),B(w.polygonOffset,w.polygonOffsetFactor,w.polygonOffsetUnits),w.alphaToCoverage===!0?Ee(r.SAMPLE_ALPHA_TO_COVERAGE):Le(r.SAMPLE_ALPHA_TO_COVERAGE)},setFlipSided:D,setCullFace:$,setLineWidth:function(w){w!==H&&(ne&&r.lineWidth(w),H=w)},setPolygonOffset:B,setScissorTest:function(w){w?Ee(r.SCISSOR_TEST):Le(r.SCISSOR_TEST)},activeTexture:function(w){w===void 0&&(w=r.TEXTURE0+te-1),ie!==w&&(r.activeTexture(w),ie=w)},bindTexture:function(w,k,Y){Y===void 0&&(Y=ie===null?r.TEXTURE0+te-1:ie);let K=re[Y];K===void 0&&(K={type:void 0,texture:void 0},re[Y]=K),K.type===w&&K.texture===k||(ie!==Y&&(r.activeTexture(Y),ie=Y),r.bindTexture(w,k||ve[w]),K.type=w,K.texture=k)},unbindTexture:function(){let w=re[ie];w!==void 0&&w.type!==void 0&&(r.bindTexture(w.type,null),w.type=void 0,w.texture=void 0)},compressedTexImage2D:function(){try{r.compressedTexImage2D(...arguments)}catch(w){rt("WebGLState:",w)}},compressedTexImage3D:function(){try{r.compressedTexImage3D(...arguments)}catch(w){rt("WebGLState:",w)}},texImage2D:function(){try{r.texImage2D(...arguments)}catch(w){rt("WebGLState:",w)}},texImage3D:function(){try{r.texImage3D(...arguments)}catch(w){rt("WebGLState:",w)}},updateUBOMapping:function(w,k){let Y=a.get(k);Y===void 0&&(Y=new WeakMap,a.set(k,Y));let K=Y.get(w);K===void 0&&(K=r.getUniformBlockIndex(k,w.name),Y.set(w,K))},uniformBlockBinding:function(w,k){let Y=a.get(k).get(w);s.get(k)!==Y&&(r.uniformBlockBinding(k,Y,w.__bindingPointIndex),s.set(k,Y))},texStorage2D:function(){try{r.texStorage2D(...arguments)}catch(w){rt("WebGLState:",w)}},texStorage3D:function(){try{r.texStorage3D(...arguments)}catch(w){rt("WebGLState:",w)}},texSubImage2D:function(){try{r.texSubImage2D(...arguments)}catch(w){rt("WebGLState:",w)}},texSubImage3D:function(){try{r.texSubImage3D(...arguments)}catch(w){rt("WebGLState:",w)}},compressedTexSubImage2D:function(){try{r.compressedTexSubImage2D(...arguments)}catch(w){rt("WebGLState:",w)}},compressedTexSubImage3D:function(){try{r.compressedTexSubImage3D(...arguments)}catch(w){rt("WebGLState:",w)}},scissor:function(w){ze.equals(w)===!1&&(r.scissor(w.x,w.y,w.z,w.w),ze.copy(w))},viewport:function(w){Ae.equals(w)===!1&&(r.viewport(w.x,w.y,w.z,w.w),Ae.copy(w))},reset:function(){r.disable(r.BLEND),r.disable(r.CULL_FACE),r.disable(r.DEPTH_TEST),r.disable(r.POLYGON_OFFSET_FILL),r.disable(r.SCISSOR_TEST),r.disable(r.STENCIL_TEST),r.disable(r.SAMPLE_ALPHA_TO_COVERAGE),r.blendEquation(r.FUNC_ADD),r.blendFunc(r.ONE,r.ZERO),r.blendFuncSeparate(r.ONE,r.ZERO,r.ONE,r.ZERO),r.blendColor(0,0,0,0),r.colorMask(!0,!0,!0,!0),r.clearColor(0,0,0,0),r.depthMask(!0),r.depthFunc(r.LESS),n.setReversed(!1),r.clearDepth(1),r.stencilMask(4294967295),r.stencilFunc(r.ALWAYS,0,4294967295),r.stencilOp(r.KEEP,r.KEEP,r.KEEP),r.clearStencil(0),r.cullFace(r.BACK),r.frontFace(r.CCW),r.polygonOffset(0,0),r.activeTexture(r.TEXTURE0),r.bindFramebuffer(r.FRAMEBUFFER,null),r.bindFramebuffer(r.DRAW_FRAMEBUFFER,null),r.bindFramebuffer(r.READ_FRAMEBUFFER,null),r.useProgram(null),r.lineWidth(1),r.scissor(0,0,r.canvas.width,r.canvas.height),r.viewport(0,0,r.canvas.width,r.canvas.height),o={},ie=null,re={},c={},l=new WeakMap,h=[],p=null,f=!1,m=null,v=null,g=null,u=null,y=null,x=null,S=null,E=new ke(0,0,0),R=0,P=!1,V=null,X=null,H=null,se=null,oe=null,ze.set(0,0,r.canvas.width,r.canvas.height),Ae.set(0,0,r.canvas.width,r.canvas.height),t.reset(),n.reset(),i.reset()}}}function Mg(r,e,t,n,i,s,a){let o=e.has("WEBGL_multisampled_render_to_texture")?e.get("WEBGL_multisampled_render_to_texture"):null,c=typeof navigator<"u"&&/OculusBrowser/g.test(navigator.userAgent),l=new Te,h=new WeakMap,p,f=new WeakMap,m=!1;try{m=typeof OffscreenCanvas<"u"&&new OffscreenCanvas(1,1).getContext("2d")!==null}catch{}function v(U,D){return m?new OffscreenCanvas(U,D):_s("canvas")}function g(U,D,$){let B=1,w=We(U);if((w.width>$||w.height>$)&&(B=$/Math.max(w.width,w.height)),B<1){if(typeof HTMLImageElement<"u"&&U instanceof HTMLImageElement||typeof HTMLCanvasElement<"u"&&U instanceof HTMLCanvasElement||typeof ImageBitmap<"u"&&U instanceof ImageBitmap||typeof VideoFrame<"u"&&U instanceof VideoFrame){let k=Math.floor(B*w.width),Y=Math.floor(B*w.height);p===void 0&&(p=v(k,Y));let K=D?v(k,Y):p;return K.width=k,K.height=Y,K.getContext("2d").drawImage(U,0,0,k,Y),tt("WebGLRenderer: Texture has been resized from ("+w.width+"x"+w.height+") to ("+k+"x"+Y+")."),K}return"data"in U&&tt("WebGLRenderer: Image in DataTexture is too big ("+w.width+"x"+w.height+")."),U}return U}function u(U){return U.generateMipmaps}function y(U){r.generateMipmap(U)}function x(U){return U.isWebGLCubeRenderTarget?r.TEXTURE_CUBE_MAP:U.isWebGL3DRenderTarget?r.TEXTURE_3D:U.isWebGLArrayRenderTarget||U.isCompressedArrayTexture?r.TEXTURE_2D_ARRAY:r.TEXTURE_2D}function S(U,D,$,B,w=!1){if(U!==null){if(r[U]!==void 0)return r[U];tt("WebGLRenderer: Attempt to use non-existing WebGL internal format '"+U+"'")}let k=D;if(D===r.RED&&($===r.FLOAT&&(k=r.R32F),$===r.HALF_FLOAT&&(k=r.R16F),$===r.UNSIGNED_BYTE&&(k=r.R8)),D===r.RED_INTEGER&&($===r.UNSIGNED_BYTE&&(k=r.R8UI),$===r.UNSIGNED_SHORT&&(k=r.R16UI),$===r.UNSIGNED_INT&&(k=r.R32UI),$===r.BYTE&&(k=r.R8I),$===r.SHORT&&(k=r.R16I),$===r.INT&&(k=r.R32I)),D===r.RG&&($===r.FLOAT&&(k=r.RG32F),$===r.HALF_FLOAT&&(k=r.RG16F),$===r.UNSIGNED_BYTE&&(k=r.RG8)),D===r.RG_INTEGER&&($===r.UNSIGNED_BYTE&&(k=r.RG8UI),$===r.UNSIGNED_SHORT&&(k=r.RG16UI),$===r.UNSIGNED_INT&&(k=r.RG32UI),$===r.BYTE&&(k=r.RG8I),$===r.SHORT&&(k=r.RG16I),$===r.INT&&(k=r.RG32I)),D===r.RGB_INTEGER&&($===r.UNSIGNED_BYTE&&(k=r.RGB8UI),$===r.UNSIGNED_SHORT&&(k=r.RGB16UI),$===r.UNSIGNED_INT&&(k=r.RGB32UI),$===r.BYTE&&(k=r.RGB8I),$===r.SHORT&&(k=r.RGB16I),$===r.INT&&(k=r.RGB32I)),D===r.RGBA_INTEGER&&($===r.UNSIGNED_BYTE&&(k=r.RGBA8UI),$===r.UNSIGNED_SHORT&&(k=r.RGBA16UI),$===r.UNSIGNED_INT&&(k=r.RGBA32UI),$===r.BYTE&&(k=r.RGBA8I),$===r.SHORT&&(k=r.RGBA16I),$===r.INT&&(k=r.RGBA32I)),D===r.RGB&&($===r.UNSIGNED_INT_5_9_9_9_REV&&(k=r.RGB9_E5),$===r.UNSIGNED_INT_10F_11F_11F_REV&&(k=r.R11F_G11F_B10F)),D===r.RGBA){let Y=w?va:Mt.getTransfer(B);$===r.FLOAT&&(k=r.RGBA32F),$===r.HALF_FLOAT&&(k=r.RGBA16F),$===r.UNSIGNED_BYTE&&(k=Y===bt?r.SRGB8_ALPHA8:r.RGBA8),$===r.UNSIGNED_SHORT_4_4_4_4&&(k=r.RGBA4),$===r.UNSIGNED_SHORT_5_5_5_1&&(k=r.RGB5_A1)}return k!==r.R16F&&k!==r.R32F&&k!==r.RG16F&&k!==r.RG32F&&k!==r.RGBA16F&&k!==r.RGBA32F||e.get("EXT_color_buffer_float"),k}function E(U,D){let $;return U?D===null||D===qi||D===zs?$=r.DEPTH24_STENCIL8:D===si?$=r.DEPTH32F_STENCIL8:D===Bs&&($=r.DEPTH24_STENCIL8,tt("DepthTexture: 16 bit depth attachment is not supported with stencil. Using 24-bit attachment.")):D===null||D===qi||D===zs?$=r.DEPTH_COMPONENT24:D===si?$=r.DEPTH_COMPONENT32F:D===Bs&&($=r.DEPTH_COMPONENT16),$}function R(U,D){return u(U)===!0||U.isFramebufferTexture&&U.minFilter!==ni&&U.minFilter!==nn?Math.log2(Math.max(D.width,D.height))+1:U.mipmaps!==void 0&&U.mipmaps.length>0?U.mipmaps.length:U.isCompressedTexture&&Array.isArray(U.image)?D.mipmaps.length:1}function P(U){let D=U.target;D.removeEventListener("dispose",P),function($){let B=n.get($);if(B.__webglInit===void 0)return;let w=$.source,k=f.get(w);if(k){let Y=k[B.__cacheKey];Y.usedTimes--,Y.usedTimes===0&&X($),Object.keys(k).length===0&&f.delete(w)}n.remove($)}(D),D.isVideoTexture&&h.delete(D)}function V(U){let D=U.target;D.removeEventListener("dispose",V),function($){let B=n.get($);if($.depthTexture&&($.depthTexture.dispose(),n.remove($.depthTexture)),$.isWebGLCubeRenderTarget)for(let k=0;k<6;k++){if(Array.isArray(B.__webglFramebuffer[k]))for(let Y=0;Y<B.__webglFramebuffer[k].length;Y++)r.deleteFramebuffer(B.__webglFramebuffer[k][Y]);else r.deleteFramebuffer(B.__webglFramebuffer[k]);B.__webglDepthbuffer&&r.deleteRenderbuffer(B.__webglDepthbuffer[k])}else{if(Array.isArray(B.__webglFramebuffer))for(let k=0;k<B.__webglFramebuffer.length;k++)r.deleteFramebuffer(B.__webglFramebuffer[k]);else r.deleteFramebuffer(B.__webglFramebuffer);if(B.__webglDepthbuffer&&r.deleteRenderbuffer(B.__webglDepthbuffer),B.__webglMultisampledFramebuffer&&r.deleteFramebuffer(B.__webglMultisampledFramebuffer),B.__webglColorRenderbuffer)for(let k=0;k<B.__webglColorRenderbuffer.length;k++)B.__webglColorRenderbuffer[k]&&r.deleteRenderbuffer(B.__webglColorRenderbuffer[k]);B.__webglDepthRenderbuffer&&r.deleteRenderbuffer(B.__webglDepthRenderbuffer)}let w=$.textures;for(let k=0,Y=w.length;k<Y;k++){let K=n.get(w[k]);K.__webglTexture&&(r.deleteTexture(K.__webglTexture),a.memory.textures--),n.remove(w[k])}n.remove($)}(D)}function X(U){let D=n.get(U);r.deleteTexture(D.__webglTexture);let $=U.source;delete f.get($)[D.__cacheKey],a.memory.textures--}let H=0;function se(U,D){let $=n.get(U);if(U.isVideoTexture&&function(B){let w=a.render.frame;h.get(B)!==w&&(h.set(B,w),B.update())}(U),U.isRenderTargetTexture===!1&&U.isExternalTexture!==!0&&U.version>0&&$.__version!==U.version){let B=U.image;if(B===null)tt("WebGLRenderer: Texture marked for update but no image data found.");else{if(B.complete!==!1)return void re($,U,D);tt("WebGLRenderer: Texture marked for update but image is incomplete")}}else U.isExternalTexture&&($.__webglTexture=U.sourceTexture?U.sourceTexture:null);t.bindTexture(r.TEXTURE_2D,$.__webglTexture,r.TEXTURE0+D)}let oe={[ms]:r.REPEAT,[ar]:r.CLAMP_TO_EDGE,[il]:r.MIRRORED_REPEAT},te={[ni]:r.NEAREST,[Bp]:r.NEAREST_MIPMAP_NEAREST,[$a]:r.NEAREST_MIPMAP_LINEAR,[nn]:r.LINEAR,[ec]:r.LINEAR_MIPMAP_NEAREST,[Nr]:r.LINEAR_MIPMAP_LINEAR},ne={[Vp]:r.NEVER,[Yp]:r.ALWAYS,[Wp]:r.LESS,[hc]:r.LEQUAL,[jp]:r.EQUAL,[uc]:r.GEQUAL,[Xp]:r.GREATER,[qp]:r.NOTEQUAL};function j(U,D){if(D.type!==si||e.has("OES_texture_float_linear")!==!1||D.magFilter!==nn&&D.magFilter!==ec&&D.magFilter!==$a&&D.magFilter!==Nr&&D.minFilter!==nn&&D.minFilter!==ec&&D.minFilter!==$a&&D.minFilter!==Nr||tt("WebGLRenderer: Unable to use linear filtering with floating point textures. OES_texture_float_linear not supported on this device."),r.texParameteri(U,r.TEXTURE_WRAP_S,oe[D.wrapS]),r.texParameteri(U,r.TEXTURE_WRAP_T,oe[D.wrapT]),U!==r.TEXTURE_3D&&U!==r.TEXTURE_2D_ARRAY||r.texParameteri(U,r.TEXTURE_WRAP_R,oe[D.wrapR]),r.texParameteri(U,r.TEXTURE_MAG_FILTER,te[D.magFilter]),r.texParameteri(U,r.TEXTURE_MIN_FILTER,te[D.minFilter]),D.compareFunction&&(r.texParameteri(U,r.TEXTURE_COMPARE_MODE,r.COMPARE_REF_TO_TEXTURE),r.texParameteri(U,r.TEXTURE_COMPARE_FUNC,ne[D.compareFunction])),e.has("EXT_texture_filter_anisotropic")===!0){if(D.magFilter===ni||D.minFilter!==$a&&D.minFilter!==Nr||D.type===si&&e.has("OES_texture_float_linear")===!1)return;if(D.anisotropy>1||n.get(D).__currentAnisotropy){let $=e.get("EXT_texture_filter_anisotropic");r.texParameterf(U,$.TEXTURE_MAX_ANISOTROPY_EXT,Math.min(D.anisotropy,i.getMaxAnisotropy())),n.get(D).__currentAnisotropy=D.anisotropy}}}function J(U,D){let $=!1;U.__webglInit===void 0&&(U.__webglInit=!0,D.addEventListener("dispose",P));let B=D.source,w=f.get(B);w===void 0&&(w={},f.set(B,w));let k=function(Y){let K=[];return K.push(Y.wrapS),K.push(Y.wrapT),K.push(Y.wrapR||0),K.push(Y.magFilter),K.push(Y.minFilter),K.push(Y.anisotropy),K.push(Y.internalFormat),K.push(Y.format),K.push(Y.type),K.push(Y.generateMipmaps),K.push(Y.premultiplyAlpha),K.push(Y.flipY),K.push(Y.unpackAlignment),K.push(Y.colorSpace),K.join()}(D);if(k!==U.__cacheKey){w[k]===void 0&&(w[k]={texture:r.createTexture(),usedTimes:0},a.memory.textures++,$=!0),w[k].usedTimes++;let Y=w[U.__cacheKey];Y!==void 0&&(w[U.__cacheKey].usedTimes--,Y.usedTimes===0&&X(D)),U.__cacheKey=k,U.__webglTexture=w[k].texture}return $}function ie(U,D,$){return Math.floor(Math.floor(U/$)/D)}function re(U,D,$){let B=r.TEXTURE_2D;(D.isDataArrayTexture||D.isCompressedArrayTexture)&&(B=r.TEXTURE_2D_ARRAY),D.isData3DTexture&&(B=r.TEXTURE_3D);let w=J(U,D),k=D.source;t.bindTexture(B,U.__webglTexture,r.TEXTURE0+$);let Y=n.get(k);if(k.version!==Y.__version||w===!0){t.activeTexture(r.TEXTURE0+$);let K=Mt.getPrimaries(Mt.workingColorSpace),T=D.colorSpace===Fr?null:Mt.getPrimaries(D.colorSpace),G=D.colorSpace===Fr||K===T?r.NONE:r.BROWSER_DEFAULT_WEBGL;r.pixelStorei(r.UNPACK_FLIP_Y_WEBGL,D.flipY),r.pixelStorei(r.UNPACK_PREMULTIPLY_ALPHA_WEBGL,D.premultiplyAlpha),r.pixelStorei(r.UNPACK_ALIGNMENT,D.unpackAlignment),r.pixelStorei(r.UNPACK_COLORSPACE_CONVERSION_WEBGL,G);let L=g(D.image,!1,i.maxTextureSize);L=Ye(D,L);let C=s.convert(D.format,D.colorSpace),z=s.convert(D.type),O,W=S(D.internalFormat,C,z,D.colorSpace,D.isVideoTexture);j(B,D);let Q=D.mipmaps,xe=D.isVideoTexture!==!0,fe=Y.__version===void 0||w===!0,Pe=k.dataReady,Be=R(D,L);if(D.isDepthTexture)W=E(D.format===Or,D.type),fe&&(xe?t.texStorage2D(r.TEXTURE_2D,1,W,L.width,L.height):t.texImage2D(r.TEXTURE_2D,0,W,L.width,L.height,0,C,z,null));else if(D.isDataTexture)if(Q.length>0){xe&&fe&&t.texStorage2D(r.TEXTURE_2D,Be,W,Q[0].width,Q[0].height);for(let he=0,me=Q.length;he<me;he++)O=Q[he],xe?Pe&&t.texSubImage2D(r.TEXTURE_2D,he,0,0,O.width,O.height,C,z,O.data):t.texImage2D(r.TEXTURE_2D,he,W,O.width,O.height,0,C,z,O.data);D.generateMipmaps=!1}else xe?(fe&&t.texStorage2D(r.TEXTURE_2D,Be,W,L.width,L.height),Pe&&function(he,me,M,ge){let ee=he.updateRanges;if(ee.length===0)t.texSubImage2D(r.TEXTURE_2D,0,0,0,me.width,me.height,M,ge,me.data);else{ee.sort((je,Ne)=>je.start-Ne.start);let De=0;for(let je=1;je<ee.length;je++){let Ne=ee[De],nt=ee[je],yt=Ne.start+Ne.count,q=ie(nt.start,me.width,4),qe=ie(Ne.start,me.width,4);nt.start<=yt+1&&q===qe&&ie(nt.start+nt.count-1,me.width,4)===q?Ne.count=Math.max(Ne.count,nt.start+nt.count-Ne.start):(++De,ee[De]=nt)}ee.length=De+1;let Ce=r.getParameter(r.UNPACK_ROW_LENGTH),Ke=r.getParameter(r.UNPACK_SKIP_PIXELS),Je=r.getParameter(r.UNPACK_SKIP_ROWS);r.pixelStorei(r.UNPACK_ROW_LENGTH,me.width);for(let je=0,Ne=ee.length;je<Ne;je++){let nt=ee[je],yt=Math.floor(nt.start/4),q=Math.ceil(nt.count/4),qe=yt%me.width,Oe=Math.floor(yt/me.width),Qe=q;r.pixelStorei(r.UNPACK_SKIP_PIXELS,qe),r.pixelStorei(r.UNPACK_SKIP_ROWS,Oe),t.texSubImage2D(r.TEXTURE_2D,0,qe,Oe,Qe,1,M,ge,me.data)}he.clearUpdateRanges(),r.pixelStorei(r.UNPACK_ROW_LENGTH,Ce),r.pixelStorei(r.UNPACK_SKIP_PIXELS,Ke),r.pixelStorei(r.UNPACK_SKIP_ROWS,Je)}}(D,L,C,z)):t.texImage2D(r.TEXTURE_2D,0,W,L.width,L.height,0,C,z,L.data);else if(D.isCompressedTexture)if(D.isCompressedArrayTexture){xe&&fe&&t.texStorage3D(r.TEXTURE_2D_ARRAY,Be,W,Q[0].width,Q[0].height,L.depth);for(let he=0,me=Q.length;he<me;he++)if(O=Q[he],D.format!==fi)if(C!==null)if(xe){if(Pe)if(D.layerUpdates.size>0){let M=Bu(O.width,O.height,D.format,D.type);for(let ge of D.layerUpdates){let ee=O.data.subarray(ge*M/O.data.BYTES_PER_ELEMENT,(ge+1)*M/O.data.BYTES_PER_ELEMENT);t.compressedTexSubImage3D(r.TEXTURE_2D_ARRAY,he,0,0,ge,O.width,O.height,1,C,ee)}D.clearLayerUpdates()}else t.compressedTexSubImage3D(r.TEXTURE_2D_ARRAY,he,0,0,0,O.width,O.height,L.depth,C,O.data)}else t.compressedTexImage3D(r.TEXTURE_2D_ARRAY,he,W,O.width,O.height,L.depth,0,O.data,0,0);else tt("WebGLRenderer: Attempt to load unsupported compressed texture format in .uploadTexture()");else xe?Pe&&t.texSubImage3D(r.TEXTURE_2D_ARRAY,he,0,0,0,O.width,O.height,L.depth,C,z,O.data):t.texImage3D(r.TEXTURE_2D_ARRAY,he,W,O.width,O.height,L.depth,0,C,z,O.data)}else{xe&&fe&&t.texStorage2D(r.TEXTURE_2D,Be,W,Q[0].width,Q[0].height);for(let he=0,me=Q.length;he<me;he++)O=Q[he],D.format!==fi?C!==null?xe?Pe&&t.compressedTexSubImage2D(r.TEXTURE_2D,he,0,0,O.width,O.height,C,O.data):t.compressedTexImage2D(r.TEXTURE_2D,he,W,O.width,O.height,0,O.data):tt("WebGLRenderer: Attempt to load unsupported compressed texture format in .uploadTexture()"):xe?Pe&&t.texSubImage2D(r.TEXTURE_2D,he,0,0,O.width,O.height,C,z,O.data):t.texImage2D(r.TEXTURE_2D,he,W,O.width,O.height,0,C,z,O.data)}else if(D.isDataArrayTexture)if(xe){if(fe&&t.texStorage3D(r.TEXTURE_2D_ARRAY,Be,W,L.width,L.height,L.depth),Pe)if(D.layerUpdates.size>0){let he=Bu(L.width,L.height,D.format,D.type);for(let me of D.layerUpdates){let M=L.data.subarray(me*he/L.data.BYTES_PER_ELEMENT,(me+1)*he/L.data.BYTES_PER_ELEMENT);t.texSubImage3D(r.TEXTURE_2D_ARRAY,0,0,0,me,L.width,L.height,1,C,z,M)}D.clearLayerUpdates()}else t.texSubImage3D(r.TEXTURE_2D_ARRAY,0,0,0,0,L.width,L.height,L.depth,C,z,L.data)}else t.texImage3D(r.TEXTURE_2D_ARRAY,0,W,L.width,L.height,L.depth,0,C,z,L.data);else if(D.isData3DTexture)xe?(fe&&t.texStorage3D(r.TEXTURE_3D,Be,W,L.width,L.height,L.depth),Pe&&t.texSubImage3D(r.TEXTURE_3D,0,0,0,0,L.width,L.height,L.depth,C,z,L.data)):t.texImage3D(r.TEXTURE_3D,0,W,L.width,L.height,L.depth,0,C,z,L.data);else if(D.isFramebufferTexture){if(fe)if(xe)t.texStorage2D(r.TEXTURE_2D,Be,W,L.width,L.height);else{let he=L.width,me=L.height;for(let M=0;M<Be;M++)t.texImage2D(r.TEXTURE_2D,M,W,he,me,0,C,z,null),he>>=1,me>>=1}}else if(Q.length>0){if(xe&&fe){let he=We(Q[0]);t.texStorage2D(r.TEXTURE_2D,Be,W,he.width,he.height)}for(let he=0,me=Q.length;he<me;he++)O=Q[he],xe?Pe&&t.texSubImage2D(r.TEXTURE_2D,he,0,0,C,z,O):t.texImage2D(r.TEXTURE_2D,he,W,C,z,O);D.generateMipmaps=!1}else if(xe){if(fe){let he=We(L);t.texStorage2D(r.TEXTURE_2D,Be,W,he.width,he.height)}Pe&&t.texSubImage2D(r.TEXTURE_2D,0,0,0,C,z,L)}else t.texImage2D(r.TEXTURE_2D,0,W,C,z,L);u(D)&&y(B),Y.__version=k.version,D.onUpdate&&D.onUpdate(D)}U.__version=D.version}function Se(U,D,$,B,w,k){let Y=s.convert($.format,$.colorSpace),K=s.convert($.type),T=S($.internalFormat,Y,K,$.colorSpace),G=n.get(D),L=n.get($);if(L.__renderTarget=D,!G.__hasExternalTextures){let C=Math.max(1,D.width>>k),z=Math.max(1,D.height>>k);w===r.TEXTURE_3D||w===r.TEXTURE_2D_ARRAY?t.texImage3D(w,k,T,C,z,D.depth,0,Y,K,null):t.texImage2D(w,k,T,C,z,0,Y,K,null)}t.bindFramebuffer(r.FRAMEBUFFER,U),Le(D)?o.framebufferTexture2DMultisampleEXT(r.FRAMEBUFFER,B,w,L.__webglTexture,0,Ee(D)):(w===r.TEXTURE_2D||w>=r.TEXTURE_CUBE_MAP_POSITIVE_X&&w<=r.TEXTURE_CUBE_MAP_NEGATIVE_Z)&&r.framebufferTexture2D(r.FRAMEBUFFER,B,w,L.__webglTexture,k),t.bindFramebuffer(r.FRAMEBUFFER,null)}function we(U,D,$){if(r.bindRenderbuffer(r.RENDERBUFFER,U),D.depthBuffer){let B=D.depthTexture,w=B&&B.isDepthTexture?B.type:null,k=E(D.stencilBuffer,w),Y=D.stencilBuffer?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT;Le(D)?o.renderbufferStorageMultisampleEXT(r.RENDERBUFFER,Ee(D),k,D.width,D.height):$?r.renderbufferStorageMultisample(r.RENDERBUFFER,Ee(D),k,D.width,D.height):r.renderbufferStorage(r.RENDERBUFFER,k,D.width,D.height),r.framebufferRenderbuffer(r.FRAMEBUFFER,Y,r.RENDERBUFFER,U)}else{let B=D.textures;for(let w=0;w<B.length;w++){let k=B[w],Y=s.convert(k.format,k.colorSpace),K=s.convert(k.type),T=S(k.internalFormat,Y,K,k.colorSpace);Le(D)?o.renderbufferStorageMultisampleEXT(r.RENDERBUFFER,Ee(D),T,D.width,D.height):$?r.renderbufferStorageMultisample(r.RENDERBUFFER,Ee(D),T,D.width,D.height):r.renderbufferStorage(r.RENDERBUFFER,T,D.width,D.height)}}r.bindRenderbuffer(r.RENDERBUFFER,null)}function ze(U,D,$){let B=D.isWebGLCubeRenderTarget===!0;if(t.bindFramebuffer(r.FRAMEBUFFER,U),!D.depthTexture||!D.depthTexture.isDepthTexture)throw new Error("renderTarget.depthTexture must be an instance of THREE.DepthTexture");let w=n.get(D.depthTexture);if(w.__renderTarget=D,w.__webglTexture&&D.depthTexture.image.width===D.width&&D.depthTexture.image.height===D.height||(D.depthTexture.image.width=D.width,D.depthTexture.image.height=D.height,D.depthTexture.needsUpdate=!0),B){if(w.__webglInit===void 0&&(w.__webglInit=!0,D.depthTexture.addEventListener("dispose",P)),w.__webglTexture===void 0){w.__webglTexture=r.createTexture(),t.bindTexture(r.TEXTURE_CUBE_MAP,w.__webglTexture),j(r.TEXTURE_CUBE_MAP,D.depthTexture);let G=s.convert(D.depthTexture.format),L=s.convert(D.depthTexture.type),C;D.depthTexture.format===pr?C=r.DEPTH_COMPONENT24:D.depthTexture.format===Or&&(C=r.DEPTH24_STENCIL8);for(let z=0;z<6;z++)r.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+z,0,C,D.width,D.height,0,G,L,null)}}else se(D.depthTexture,0);let k=w.__webglTexture,Y=Ee(D),K=B?r.TEXTURE_CUBE_MAP_POSITIVE_X+$:r.TEXTURE_2D,T=D.depthTexture.format===Or?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT;if(D.depthTexture.format===pr)Le(D)?o.framebufferTexture2DMultisampleEXT(r.FRAMEBUFFER,T,K,k,0,Y):r.framebufferTexture2D(r.FRAMEBUFFER,T,K,k,0);else{if(D.depthTexture.format!==Or)throw new Error("Unknown depthTexture format");Le(D)?o.framebufferTexture2DMultisampleEXT(r.FRAMEBUFFER,T,K,k,0,Y):r.framebufferTexture2D(r.FRAMEBUFFER,T,K,k,0)}}function Ae(U){let D=n.get(U),$=U.isWebGLCubeRenderTarget===!0;if(D.__boundDepthTexture!==U.depthTexture){let B=U.depthTexture;if(D.__depthDisposeCallback&&D.__depthDisposeCallback(),B){let w=()=>{delete D.__boundDepthTexture,delete D.__depthDisposeCallback,B.removeEventListener("dispose",w)};B.addEventListener("dispose",w),D.__depthDisposeCallback=w}D.__boundDepthTexture=B}if(U.depthTexture&&!D.__autoAllocateDepthBuffer)if($)for(let B=0;B<6;B++)ze(D.__webglFramebuffer[B],U,B);else{let B=U.texture.mipmaps;B&&B.length>0?ze(D.__webglFramebuffer[0],U,0):ze(D.__webglFramebuffer,U,0)}else if($){D.__webglDepthbuffer=[];for(let B=0;B<6;B++)if(t.bindFramebuffer(r.FRAMEBUFFER,D.__webglFramebuffer[B]),D.__webglDepthbuffer[B]===void 0)D.__webglDepthbuffer[B]=r.createRenderbuffer(),we(D.__webglDepthbuffer[B],U,!1);else{let w=U.stencilBuffer?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT,k=D.__webglDepthbuffer[B];r.bindRenderbuffer(r.RENDERBUFFER,k),r.framebufferRenderbuffer(r.FRAMEBUFFER,w,r.RENDERBUFFER,k)}}else{let B=U.texture.mipmaps;if(B&&B.length>0?t.bindFramebuffer(r.FRAMEBUFFER,D.__webglFramebuffer[0]):t.bindFramebuffer(r.FRAMEBUFFER,D.__webglFramebuffer),D.__webglDepthbuffer===void 0)D.__webglDepthbuffer=r.createRenderbuffer(),we(D.__webglDepthbuffer,U,!1);else{let w=U.stencilBuffer?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT,k=D.__webglDepthbuffer;r.bindRenderbuffer(r.RENDERBUFFER,k),r.framebufferRenderbuffer(r.FRAMEBUFFER,w,r.RENDERBUFFER,k)}}t.bindFramebuffer(r.FRAMEBUFFER,null)}let Ie=[],ve=[];function Ee(U){return Math.min(i.maxSamples,U.samples)}function Le(U){let D=n.get(U);return U.samples>0&&e.has("WEBGL_multisampled_render_to_texture")===!0&&D.__useRenderToTexture!==!1}function Ye(U,D){let $=U.colorSpace,B=U.format,w=U.type;return U.isCompressedTexture===!0||U.isVideoTexture===!0||$!==Rr&&$!==Fr&&(Mt.getTransfer($)===bt?B===fi&&w===Yn||tt("WebGLTextures: sRGB encoded textures have to use RGBAFormat and UnsignedByteType."):rt("WebGLTextures: Unsupported texture color space:",$)),D}function We(U){return typeof HTMLImageElement<"u"&&U instanceof HTMLImageElement?(l.width=U.naturalWidth||U.width,l.height=U.naturalHeight||U.height):typeof VideoFrame<"u"&&U instanceof VideoFrame?(l.width=U.displayWidth,l.height=U.displayHeight):(l.width=U.width,l.height=U.height),l}this.allocateTextureUnit=function(){let U=H;return U>=i.maxTextures&&tt("WebGLTextures: Trying to use "+U+" texture units while this GPU supports only "+i.maxTextures),H+=1,U},this.resetTextureUnits=function(){H=0},this.setTexture2D=se,this.setTexture2DArray=function(U,D){let $=n.get(U);U.isRenderTargetTexture===!1&&U.version>0&&$.__version!==U.version?re($,U,D):(U.isExternalTexture&&($.__webglTexture=U.sourceTexture?U.sourceTexture:null),t.bindTexture(r.TEXTURE_2D_ARRAY,$.__webglTexture,r.TEXTURE0+D))},this.setTexture3D=function(U,D){let $=n.get(U);U.isRenderTargetTexture===!1&&U.version>0&&$.__version!==U.version?re($,U,D):t.bindTexture(r.TEXTURE_3D,$.__webglTexture,r.TEXTURE0+D)},this.setTextureCube=function(U,D){let $=n.get(U);U.isCubeDepthTexture!==!0&&U.version>0&&$.__version!==U.version?function(B,w,k){if(w.image.length!==6)return;let Y=J(B,w),K=w.source;t.bindTexture(r.TEXTURE_CUBE_MAP,B.__webglTexture,r.TEXTURE0+k);let T=n.get(K);if(K.version!==T.__version||Y===!0){t.activeTexture(r.TEXTURE0+k);let G=Mt.getPrimaries(Mt.workingColorSpace),L=w.colorSpace===Fr?null:Mt.getPrimaries(w.colorSpace),C=w.colorSpace===Fr||G===L?r.NONE:r.BROWSER_DEFAULT_WEBGL;r.pixelStorei(r.UNPACK_FLIP_Y_WEBGL,w.flipY),r.pixelStorei(r.UNPACK_PREMULTIPLY_ALPHA_WEBGL,w.premultiplyAlpha),r.pixelStorei(r.UNPACK_ALIGNMENT,w.unpackAlignment),r.pixelStorei(r.UNPACK_COLORSPACE_CONVERSION_WEBGL,C);let z=w.isCompressedTexture||w.image[0].isCompressedTexture,O=w.image[0]&&w.image[0].isDataTexture,W=[];for(let ee=0;ee<6;ee++)W[ee]=z||O?O?w.image[ee].image:w.image[ee]:g(w.image[ee],!0,i.maxCubemapSize),W[ee]=Ye(w,W[ee]);let Q=W[0],xe=s.convert(w.format,w.colorSpace),fe=s.convert(w.type),Pe=S(w.internalFormat,xe,fe,w.colorSpace),Be=w.isVideoTexture!==!0,he=T.__version===void 0||Y===!0,me=K.dataReady,M,ge=R(w,Q);if(j(r.TEXTURE_CUBE_MAP,w),z){Be&&he&&t.texStorage2D(r.TEXTURE_CUBE_MAP,ge,Pe,Q.width,Q.height);for(let ee=0;ee<6;ee++){M=W[ee].mipmaps;for(let De=0;De<M.length;De++){let Ce=M[De];w.format!==fi?xe!==null?Be?me&&t.compressedTexSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De,0,0,Ce.width,Ce.height,xe,Ce.data):t.compressedTexImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De,Pe,Ce.width,Ce.height,0,Ce.data):tt("WebGLRenderer: Attempt to load unsupported compressed texture format in .setTextureCube()"):Be?me&&t.texSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De,0,0,Ce.width,Ce.height,xe,fe,Ce.data):t.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De,Pe,Ce.width,Ce.height,0,xe,fe,Ce.data)}}}else{if(M=w.mipmaps,Be&&he){M.length>0&&ge++;let ee=We(W[0]);t.texStorage2D(r.TEXTURE_CUBE_MAP,ge,Pe,ee.width,ee.height)}for(let ee=0;ee<6;ee++)if(O){Be?me&&t.texSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,0,0,0,W[ee].width,W[ee].height,xe,fe,W[ee].data):t.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,0,Pe,W[ee].width,W[ee].height,0,xe,fe,W[ee].data);for(let De=0;De<M.length;De++){let Ce=M[De].image[ee].image;Be?me&&t.texSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De+1,0,0,Ce.width,Ce.height,xe,fe,Ce.data):t.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De+1,Pe,Ce.width,Ce.height,0,xe,fe,Ce.data)}}else{Be?me&&t.texSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,0,0,0,xe,fe,W[ee]):t.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,0,Pe,xe,fe,W[ee]);for(let De=0;De<M.length;De++){let Ce=M[De];Be?me&&t.texSubImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De+1,0,0,xe,fe,Ce.image[ee]):t.texImage2D(r.TEXTURE_CUBE_MAP_POSITIVE_X+ee,De+1,Pe,xe,fe,Ce.image[ee])}}}u(w)&&y(r.TEXTURE_CUBE_MAP),T.__version=K.version,w.onUpdate&&w.onUpdate(w)}B.__version=w.version}($,U,D):t.bindTexture(r.TEXTURE_CUBE_MAP,$.__webglTexture,r.TEXTURE0+D)},this.rebindTextures=function(U,D,$){let B=n.get(U);D!==void 0&&Se(B.__webglFramebuffer,U,U.texture,r.COLOR_ATTACHMENT0,r.TEXTURE_2D,0),$!==void 0&&Ae(U)},this.setupRenderTarget=function(U){let D=U.texture,$=n.get(U),B=n.get(D);U.addEventListener("dispose",V);let w=U.textures,k=U.isWebGLCubeRenderTarget===!0,Y=w.length>1;if(Y||(B.__webglTexture===void 0&&(B.__webglTexture=r.createTexture()),B.__version=D.version,a.memory.textures++),k){$.__webglFramebuffer=[];for(let K=0;K<6;K++)if(D.mipmaps&&D.mipmaps.length>0){$.__webglFramebuffer[K]=[];for(let T=0;T<D.mipmaps.length;T++)$.__webglFramebuffer[K][T]=r.createFramebuffer()}else $.__webglFramebuffer[K]=r.createFramebuffer()}else{if(D.mipmaps&&D.mipmaps.length>0){$.__webglFramebuffer=[];for(let K=0;K<D.mipmaps.length;K++)$.__webglFramebuffer[K]=r.createFramebuffer()}else $.__webglFramebuffer=r.createFramebuffer();if(Y)for(let K=0,T=w.length;K<T;K++){let G=n.get(w[K]);G.__webglTexture===void 0&&(G.__webglTexture=r.createTexture(),a.memory.textures++)}if(U.samples>0&&Le(U)===!1){$.__webglMultisampledFramebuffer=r.createFramebuffer(),$.__webglColorRenderbuffer=[],t.bindFramebuffer(r.FRAMEBUFFER,$.__webglMultisampledFramebuffer);for(let K=0;K<w.length;K++){let T=w[K];$.__webglColorRenderbuffer[K]=r.createRenderbuffer(),r.bindRenderbuffer(r.RENDERBUFFER,$.__webglColorRenderbuffer[K]);let G=s.convert(T.format,T.colorSpace),L=s.convert(T.type),C=S(T.internalFormat,G,L,T.colorSpace,U.isXRRenderTarget===!0),z=Ee(U);r.renderbufferStorageMultisample(r.RENDERBUFFER,z,C,U.width,U.height),r.framebufferRenderbuffer(r.FRAMEBUFFER,r.COLOR_ATTACHMENT0+K,r.RENDERBUFFER,$.__webglColorRenderbuffer[K])}r.bindRenderbuffer(r.RENDERBUFFER,null),U.depthBuffer&&($.__webglDepthRenderbuffer=r.createRenderbuffer(),we($.__webglDepthRenderbuffer,U,!0)),t.bindFramebuffer(r.FRAMEBUFFER,null)}}if(k){t.bindTexture(r.TEXTURE_CUBE_MAP,B.__webglTexture),j(r.TEXTURE_CUBE_MAP,D);for(let K=0;K<6;K++)if(D.mipmaps&&D.mipmaps.length>0)for(let T=0;T<D.mipmaps.length;T++)Se($.__webglFramebuffer[K][T],U,D,r.COLOR_ATTACHMENT0,r.TEXTURE_CUBE_MAP_POSITIVE_X+K,T);else Se($.__webglFramebuffer[K],U,D,r.COLOR_ATTACHMENT0,r.TEXTURE_CUBE_MAP_POSITIVE_X+K,0);u(D)&&y(r.TEXTURE_CUBE_MAP),t.unbindTexture()}else if(Y){for(let K=0,T=w.length;K<T;K++){let G=w[K],L=n.get(G),C=r.TEXTURE_2D;(U.isWebGL3DRenderTarget||U.isWebGLArrayRenderTarget)&&(C=U.isWebGL3DRenderTarget?r.TEXTURE_3D:r.TEXTURE_2D_ARRAY),t.bindTexture(C,L.__webglTexture),j(C,G),Se($.__webglFramebuffer,U,G,r.COLOR_ATTACHMENT0+K,C,0),u(G)&&y(C)}t.unbindTexture()}else{let K=r.TEXTURE_2D;if((U.isWebGL3DRenderTarget||U.isWebGLArrayRenderTarget)&&(K=U.isWebGL3DRenderTarget?r.TEXTURE_3D:r.TEXTURE_2D_ARRAY),t.bindTexture(K,B.__webglTexture),j(K,D),D.mipmaps&&D.mipmaps.length>0)for(let T=0;T<D.mipmaps.length;T++)Se($.__webglFramebuffer[T],U,D,r.COLOR_ATTACHMENT0,K,T);else Se($.__webglFramebuffer,U,D,r.COLOR_ATTACHMENT0,K,0);u(D)&&y(K),t.unbindTexture()}U.depthBuffer&&Ae(U)},this.updateRenderTargetMipmap=function(U){let D=U.textures;for(let $=0,B=D.length;$<B;$++){let w=D[$];if(u(w)){let k=x(U),Y=n.get(w).__webglTexture;t.bindTexture(k,Y),y(k),t.unbindTexture()}}},this.updateMultisampleRenderTarget=function(U){if(U.samples>0){if(Le(U)===!1){let D=U.textures,$=U.width,B=U.height,w=r.COLOR_BUFFER_BIT,k=U.stencilBuffer?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT,Y=n.get(U),K=D.length>1;if(K)for(let G=0;G<D.length;G++)t.bindFramebuffer(r.FRAMEBUFFER,Y.__webglMultisampledFramebuffer),r.framebufferRenderbuffer(r.FRAMEBUFFER,r.COLOR_ATTACHMENT0+G,r.RENDERBUFFER,null),t.bindFramebuffer(r.FRAMEBUFFER,Y.__webglFramebuffer),r.framebufferTexture2D(r.DRAW_FRAMEBUFFER,r.COLOR_ATTACHMENT0+G,r.TEXTURE_2D,null,0);t.bindFramebuffer(r.READ_FRAMEBUFFER,Y.__webglMultisampledFramebuffer);let T=U.texture.mipmaps;T&&T.length>0?t.bindFramebuffer(r.DRAW_FRAMEBUFFER,Y.__webglFramebuffer[0]):t.bindFramebuffer(r.DRAW_FRAMEBUFFER,Y.__webglFramebuffer);for(let G=0;G<D.length;G++){if(U.resolveDepthBuffer&&(U.depthBuffer&&(w|=r.DEPTH_BUFFER_BIT),U.stencilBuffer&&U.resolveStencilBuffer&&(w|=r.STENCIL_BUFFER_BIT)),K){r.framebufferRenderbuffer(r.READ_FRAMEBUFFER,r.COLOR_ATTACHMENT0,r.RENDERBUFFER,Y.__webglColorRenderbuffer[G]);let L=n.get(D[G]).__webglTexture;r.framebufferTexture2D(r.DRAW_FRAMEBUFFER,r.COLOR_ATTACHMENT0,r.TEXTURE_2D,L,0)}r.blitFramebuffer(0,0,$,B,0,0,$,B,w,r.NEAREST),c===!0&&(Ie.length=0,ve.length=0,Ie.push(r.COLOR_ATTACHMENT0+G),U.depthBuffer&&U.resolveDepthBuffer===!1&&(Ie.push(k),ve.push(k),r.invalidateFramebuffer(r.DRAW_FRAMEBUFFER,ve)),r.invalidateFramebuffer(r.READ_FRAMEBUFFER,Ie))}if(t.bindFramebuffer(r.READ_FRAMEBUFFER,null),t.bindFramebuffer(r.DRAW_FRAMEBUFFER,null),K)for(let G=0;G<D.length;G++){t.bindFramebuffer(r.FRAMEBUFFER,Y.__webglMultisampledFramebuffer),r.framebufferRenderbuffer(r.FRAMEBUFFER,r.COLOR_ATTACHMENT0+G,r.RENDERBUFFER,Y.__webglColorRenderbuffer[G]);let L=n.get(D[G]).__webglTexture;t.bindFramebuffer(r.FRAMEBUFFER,Y.__webglFramebuffer),r.framebufferTexture2D(r.DRAW_FRAMEBUFFER,r.COLOR_ATTACHMENT0+G,r.TEXTURE_2D,L,0)}t.bindFramebuffer(r.DRAW_FRAMEBUFFER,Y.__webglMultisampledFramebuffer)}else if(U.depthBuffer&&U.resolveDepthBuffer===!1&&c){let D=U.stencilBuffer?r.DEPTH_STENCIL_ATTACHMENT:r.DEPTH_ATTACHMENT;r.invalidateFramebuffer(r.DRAW_FRAMEBUFFER,[D])}}},this.setupDepthRenderbuffer=Ae,this.setupFrameBufferTexture=Se,this.useMultisampledRTT=Le,this.isReversedDepthBuffer=function(){return t.buffers.depth.getReversed()}}function bg(r,e){return{convert:function(t,n=Fr){let i,s=Mt.getTransfer(n);if(t===Yn)return r.UNSIGNED_BYTE;if(t===nc)return r.UNSIGNED_SHORT_4_4_4_4;if(t===ic)return r.UNSIGNED_SHORT_5_5_5_1;if(t===Kh)return r.UNSIGNED_INT_5_9_9_9_REV;if(t===$h)return r.UNSIGNED_INT_10F_11F_11F_REV;if(t===Zh)return r.BYTE;if(t===Jh)return r.SHORT;if(t===Bs)return r.UNSIGNED_SHORT;if(t===tc)return r.INT;if(t===qi)return r.UNSIGNED_INT;if(t===si)return r.FLOAT;if(t===wi)return r.HALF_FLOAT;if(t===zp)return r.ALPHA;if(t===kp)return r.RGB;if(t===fi)return r.RGBA;if(t===pr)return r.DEPTH_COMPONENT;if(t===Or)return r.DEPTH_STENCIL;if(t===rc)return r.RED;if(t===sc)return r.RED_INTEGER;if(t===ks)return r.RG;if(t===Qh)return r.RG_INTEGER;if(t===eu)return r.RGBA_INTEGER;if(t===ac||t===oc||t===lc||t===cc)if(s===bt){if(i=e.get("WEBGL_compressed_texture_s3tc_srgb"),i===null)return null;if(t===ac)return i.COMPRESSED_SRGB_S3TC_DXT1_EXT;if(t===oc)return i.COMPRESSED_SRGB_ALPHA_S3TC_DXT1_EXT;if(t===lc)return i.COMPRESSED_SRGB_ALPHA_S3TC_DXT3_EXT;if(t===cc)return i.COMPRESSED_SRGB_ALPHA_S3TC_DXT5_EXT}else{if(i=e.get("WEBGL_compressed_texture_s3tc"),i===null)return null;if(t===ac)return i.COMPRESSED_RGB_S3TC_DXT1_EXT;if(t===oc)return i.COMPRESSED_RGBA_S3TC_DXT1_EXT;if(t===lc)return i.COMPRESSED_RGBA_S3TC_DXT3_EXT;if(t===cc)return i.COMPRESSED_RGBA_S3TC_DXT5_EXT}if(t===tu||t===nu||t===iu||t===ru){if(i=e.get("WEBGL_compressed_texture_pvrtc"),i===null)return null;if(t===tu)return i.COMPRESSED_RGB_PVRTC_4BPPV1_IMG;if(t===nu)return i.COMPRESSED_RGB_PVRTC_2BPPV1_IMG;if(t===iu)return i.COMPRESSED_RGBA_PVRTC_4BPPV1_IMG;if(t===ru)return i.COMPRESSED_RGBA_PVRTC_2BPPV1_IMG}if(t===su||t===au||t===ou||t===lu||t===cu||t===hu||t===uu){if(i=e.get("WEBGL_compressed_texture_etc"),i===null)return null;if(t===su||t===au)return s===bt?i.COMPRESSED_SRGB8_ETC2:i.COMPRESSED_RGB8_ETC2;if(t===ou)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ETC2_EAC:i.COMPRESSED_RGBA8_ETC2_EAC;if(t===lu)return i.COMPRESSED_R11_EAC;if(t===cu)return i.COMPRESSED_SIGNED_R11_EAC;if(t===hu)return i.COMPRESSED_RG11_EAC;if(t===uu)return i.COMPRESSED_SIGNED_RG11_EAC}if(t===du||t===pu||t===fu||t===mu||t===gu||t===_u||t===vu||t===yu||t===xu||t===Mu||t===bu||t===Su||t===Tu||t===Eu){if(i=e.get("WEBGL_compressed_texture_astc"),i===null)return null;if(t===du)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_4x4_KHR:i.COMPRESSED_RGBA_ASTC_4x4_KHR;if(t===pu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_5x4_KHR:i.COMPRESSED_RGBA_ASTC_5x4_KHR;if(t===fu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_5x5_KHR:i.COMPRESSED_RGBA_ASTC_5x5_KHR;if(t===mu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_6x5_KHR:i.COMPRESSED_RGBA_ASTC_6x5_KHR;if(t===gu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_6x6_KHR:i.COMPRESSED_RGBA_ASTC_6x6_KHR;if(t===_u)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_8x5_KHR:i.COMPRESSED_RGBA_ASTC_8x5_KHR;if(t===vu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_8x6_KHR:i.COMPRESSED_RGBA_ASTC_8x6_KHR;if(t===yu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_8x8_KHR:i.COMPRESSED_RGBA_ASTC_8x8_KHR;if(t===xu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_10x5_KHR:i.COMPRESSED_RGBA_ASTC_10x5_KHR;if(t===Mu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_10x6_KHR:i.COMPRESSED_RGBA_ASTC_10x6_KHR;if(t===bu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_10x8_KHR:i.COMPRESSED_RGBA_ASTC_10x8_KHR;if(t===Su)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_10x10_KHR:i.COMPRESSED_RGBA_ASTC_10x10_KHR;if(t===Tu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_12x10_KHR:i.COMPRESSED_RGBA_ASTC_12x10_KHR;if(t===Eu)return s===bt?i.COMPRESSED_SRGB8_ALPHA8_ASTC_12x12_KHR:i.COMPRESSED_RGBA_ASTC_12x12_KHR}if(t===wu||t===Au||t===Cu){if(i=e.get("EXT_texture_compression_bptc"),i===null)return null;if(t===wu)return s===bt?i.COMPRESSED_SRGB_ALPHA_BPTC_UNORM_EXT:i.COMPRESSED_RGBA_BPTC_UNORM_EXT;if(t===Au)return i.COMPRESSED_RGB_BPTC_SIGNED_FLOAT_EXT;if(t===Cu)return i.COMPRESSED_RGB_BPTC_UNSIGNED_FLOAT_EXT}if(t===Ru||t===Pu||t===Iu||t===Lu){if(i=e.get("EXT_texture_compression_rgtc"),i===null)return null;if(t===Ru)return i.COMPRESSED_RED_RGTC1_EXT;if(t===Pu)return i.COMPRESSED_SIGNED_RED_RGTC1_EXT;if(t===Iu)return i.COMPRESSED_RED_GREEN_RGTC2_EXT;if(t===Lu)return i.COMPRESSED_SIGNED_RED_GREEN_RGTC2_EXT}return t===zs?r.UNSIGNED_INT_24_8:r[t]!==void 0?r[t]:null}}}var $u=class{constructor(){this.texture=null,this.mesh=null,this.depthNear=0,this.depthFar=0}init(e,t){if(this.texture===null){let n=new Pa(e.texture);e.depthNear===t.depthNear&&e.depthFar===t.depthFar||(this.depthNear=e.depthNear,this.depthFar=e.depthFar),this.texture=n}}getMesh(e){if(this.texture!==null&&this.mesh===null){let t=e.cameras[0].viewport,n=new qn({vertexShader:`
void main() {

	gl_Position = vec4( position, 1.0 );

}`,fragmentShader:`
uniform sampler2DArray depthColor;
uniform float depthWidth;
uniform float depthHeight;

void main() {

	vec2 coord = vec2( gl_FragCoord.x / depthWidth, gl_FragCoord.y / depthHeight );

	if ( coord.x >= 1.0 ) {

		gl_FragDepth = texture( depthColor, vec3( coord.x - 1.0, coord.y, 1 ) ).r;

	} else {

		gl_FragDepth = texture( depthColor, vec3( coord.x, coord.y, 0 ) ).r;

	}

}`,uniforms:{depthColor:{value:this.texture},depthWidth:{value:t.z},depthHeight:{value:t.w}}});this.mesh=new Et(new vn(20,20),n)}return this.mesh}reset(){this.texture=null,this.mesh=null}getDepthTexture(){return this.texture}},Qu=class extends bi{constructor(e,t){super();let n=this,i=null,s=1,a=null,o="local-floor",c=1,l=null,h=null,p=null,f=null,m=null,v=null,g=typeof XRWebGLBinding<"u",u=new $u,y={},x=t.getContextAttributes(),S=null,E=null,R=[],P=[],V=new Te,X=null,H=new an;H.viewport=new Lt;let se=new an;se.viewport=new Lt;let oe=[H,se],te=new Jl,ne=null,j=null;function J(ve){let Ee=P.indexOf(ve.inputSource);if(Ee===-1)return;let Le=R[Ee];Le!==void 0&&(Le.update(ve.inputSource,ve.frame,l||a),Le.dispatchEvent({type:ve.type,data:ve.inputSource}))}function ie(){i.removeEventListener("select",J),i.removeEventListener("selectstart",J),i.removeEventListener("selectend",J),i.removeEventListener("squeeze",J),i.removeEventListener("squeezestart",J),i.removeEventListener("squeezeend",J),i.removeEventListener("end",ie),i.removeEventListener("inputsourceschange",re);for(let ve=0;ve<R.length;ve++){let Ee=P[ve];Ee!==null&&(P[ve]=null,R[ve].disconnect(Ee))}ne=null,j=null,u.reset();for(let ve in y)delete y[ve];e.setRenderTarget(S),m=null,f=null,p=null,i=null,E=null,Ie.stop(),n.isPresenting=!1,e.setPixelRatio(X),e.setSize(V.width,V.height,!1),n.dispatchEvent({type:"sessionend"})}function re(ve){for(let Ee=0;Ee<ve.removed.length;Ee++){let Le=ve.removed[Ee],Ye=P.indexOf(Le);Ye>=0&&(P[Ye]=null,R[Ye].disconnect(Le))}for(let Ee=0;Ee<ve.added.length;Ee++){let Le=ve.added[Ee],Ye=P.indexOf(Le);if(Ye===-1){for(let U=0;U<R.length;U++){if(U>=P.length){P.push(Le),Ye=U;break}if(P[U]===null){P[U]=Le,Ye=U;break}}if(Ye===-1)break}let We=R[Ye];We&&We.connect(Le)}}this.cameraAutoUpdate=!0,this.enabled=!1,this.isPresenting=!1,this.getController=function(ve){let Ee=R[ve];return Ee===void 0&&(Ee=new bs,R[ve]=Ee),Ee.getTargetRaySpace()},this.getControllerGrip=function(ve){let Ee=R[ve];return Ee===void 0&&(Ee=new bs,R[ve]=Ee),Ee.getGripSpace()},this.getHand=function(ve){let Ee=R[ve];return Ee===void 0&&(Ee=new bs,R[ve]=Ee),Ee.getHandSpace()},this.setFramebufferScaleFactor=function(ve){s=ve,n.isPresenting===!0&&tt("WebXRManager: Cannot change framebuffer scale while presenting.")},this.setReferenceSpaceType=function(ve){o=ve,n.isPresenting===!0&&tt("WebXRManager: Cannot change reference space type while presenting.")},this.getReferenceSpace=function(){return l||a},this.setReferenceSpace=function(ve){l=ve},this.getBaseLayer=function(){return f!==null?f:m},this.getBinding=function(){return p===null&&g&&(p=new XRWebGLBinding(i,t)),p},this.getFrame=function(){return v},this.getSession=function(){return i},this.setSession=async function(ve){if(i=ve,i!==null){if(S=e.getRenderTarget(),i.addEventListener("select",J),i.addEventListener("selectstart",J),i.addEventListener("selectend",J),i.addEventListener("squeeze",J),i.addEventListener("squeezestart",J),i.addEventListener("squeezeend",J),i.addEventListener("end",ie),i.addEventListener("inputsourceschange",re),x.xrCompatible!==!0&&await t.makeXRCompatible(),X=e.getPixelRatio(),e.getSize(V),g&&"createProjectionLayer"in XRWebGLBinding.prototype){let Ee=null,Le=null,Ye=null;x.depth&&(Ye=x.stencil?t.DEPTH24_STENCIL8:t.DEPTH_COMPONENT24,Ee=x.stencil?Or:pr,Le=x.stencil?zs:qi);let We={colorFormat:t.RGBA8,depthFormat:Ye,scaleFactor:s};p=this.getBinding(),f=p.createProjectionLayer(We),i.updateRenderState({layers:[f]}),e.setPixelRatio(1),e.setSize(f.textureWidth,f.textureHeight,!1),E=new Vn(f.textureWidth,f.textureHeight,{format:fi,type:Yn,depthTexture:new lr(f.textureWidth,f.textureHeight,Le,void 0,void 0,void 0,void 0,void 0,void 0,Ee),stencilBuffer:x.stencil,colorSpace:e.outputColorSpace,samples:x.antialias?4:0,resolveDepthBuffer:f.ignoreDepthValues===!1,resolveStencilBuffer:f.ignoreDepthValues===!1})}else{let Ee={antialias:x.antialias,alpha:!0,depth:x.depth,stencil:x.stencil,framebufferScaleFactor:s};m=new XRWebGLLayer(i,t,Ee),i.updateRenderState({baseLayer:m}),e.setPixelRatio(1),e.setSize(m.framebufferWidth,m.framebufferHeight,!1),E=new Vn(m.framebufferWidth,m.framebufferHeight,{format:fi,type:Yn,colorSpace:e.outputColorSpace,stencilBuffer:x.stencil,resolveDepthBuffer:m.ignoreDepthValues===!1,resolveStencilBuffer:m.ignoreDepthValues===!1})}E.isXRRenderTarget=!0,this.setFoveation(c),l=null,a=await i.requestReferenceSpace(o),Ie.setContext(i),Ie.start(),n.isPresenting=!0,n.dispatchEvent({type:"sessionstart"})}},this.getEnvironmentBlendMode=function(){if(i!==null)return i.environmentBlendMode},this.getDepthTexture=function(){return u.getDepthTexture()};let Se=new N,we=new N;function ze(ve,Ee){Ee===null?ve.matrixWorld.copy(ve.matrix):ve.matrixWorld.multiplyMatrices(Ee.matrixWorld,ve.matrix),ve.matrixWorldInverse.copy(ve.matrixWorld).invert()}this.updateCamera=function(ve){if(i===null)return;let Ee=ve.near,Le=ve.far;u.texture!==null&&(u.depthNear>0&&(Ee=u.depthNear),u.depthFar>0&&(Le=u.depthFar)),te.near=se.near=H.near=Ee,te.far=se.far=H.far=Le,ne===te.near&&j===te.far||(i.updateRenderState({depthNear:te.near,depthFar:te.far}),ne=te.near,j=te.far),te.layers.mask=6|ve.layers.mask,H.layers.mask=-5&te.layers.mask,se.layers.mask=-3&te.layers.mask;let Ye=ve.parent,We=te.cameras;ze(te,Ye);for(let U=0;U<We.length;U++)ze(We[U],Ye);We.length===2?function(U,D,$){Se.setFromMatrixPosition(D.matrixWorld),we.setFromMatrixPosition($.matrixWorld);let B=Se.distanceTo(we),w=D.projectionMatrix.elements,k=$.projectionMatrix.elements,Y=w[14]/(w[10]-1),K=w[14]/(w[10]+1),T=(w[9]+1)/w[5],G=(w[9]-1)/w[5],L=(w[8]-1)/w[0],C=(k[8]+1)/k[0],z=Y*L,O=Y*C,W=B/(-L+C),Q=W*-L;if(D.matrixWorld.decompose(U.position,U.quaternion,U.scale),U.translateX(Q),U.translateZ(W),U.matrixWorld.compose(U.position,U.quaternion,U.scale),U.matrixWorldInverse.copy(U.matrixWorld).invert(),w[10]===-1)U.projectionMatrix.copy(D.projectionMatrix),U.projectionMatrixInverse.copy(D.projectionMatrixInverse);else{let xe=Y+W,fe=K+W,Pe=z-Q,Be=O+(B-Q),he=T*K/fe*xe,me=G*K/fe*xe;U.projectionMatrix.makePerspective(Pe,Be,he,me,xe,fe),U.projectionMatrixInverse.copy(U.projectionMatrix).invert()}}(te,H,se):te.projectionMatrix.copy(H.projectionMatrix),function(U,D,$){$===null?U.matrix.copy(D.matrixWorld):(U.matrix.copy($.matrixWorld),U.matrix.invert(),U.matrix.multiply(D.matrixWorld)),U.matrix.decompose(U.position,U.quaternion,U.scale),U.updateMatrixWorld(!0),U.projectionMatrix.copy(D.projectionMatrix),U.projectionMatrixInverse.copy(D.projectionMatrixInverse),U.isPerspectiveCamera&&(U.fov=2*ys*Math.atan(1/U.projectionMatrix.elements[5]),U.zoom=1)}(ve,te,Ye)},this.getCamera=function(){return te},this.getFoveation=function(){if(f!==null||m!==null)return c},this.setFoveation=function(ve){c=ve,f!==null&&(f.fixedFoveation=ve),m!==null&&m.fixedFoveation!==void 0&&(m.fixedFoveation=ve)},this.hasDepthSensing=function(){return u.texture!==null},this.getDepthSensingMesh=function(){return u.getMesh(te)},this.getCameraTexture=function(ve){return y[ve]};let Ae=null,Ie=new Cf;Ie.setAnimationLoop(function(ve,Ee){if(h=Ee.getViewerPose(l||a),v=Ee,h!==null){let Le=h.views;m!==null&&(e.setRenderTargetFramebuffer(E,m.framebuffer),e.setRenderTarget(E));let Ye=!1;Le.length!==te.cameras.length&&(te.cameras.length=0,Ye=!0);for(let U=0;U<Le.length;U++){let D=Le[U],$=null;if(m!==null)$=m.getViewport(D);else{let w=p.getViewSubImage(f,D);$=w.viewport,U===0&&(e.setRenderTargetTextures(E,w.colorTexture,w.depthStencilTexture),e.setRenderTarget(E))}let B=oe[U];B===void 0&&(B=new an,B.layers.enable(U),B.viewport=new Lt,oe[U]=B),B.matrix.fromArray(D.transform.matrix),B.matrix.decompose(B.position,B.quaternion,B.scale),B.projectionMatrix.fromArray(D.projectionMatrix),B.projectionMatrixInverse.copy(B.projectionMatrix).invert(),B.viewport.set($.x,$.y,$.width,$.height),U===0&&(te.matrix.copy(B.matrix),te.matrix.decompose(te.position,te.quaternion,te.scale)),Ye===!0&&te.cameras.push(B)}let We=i.enabledFeatures;if(We&&We.includes("depth-sensing")&&i.depthUsage=="gpu-optimized"&&g){p=n.getBinding();let U=p.getDepthInformation(Le[0]);U&&U.isValid&&U.texture&&u.init(U,i.renderState)}if(We&&We.includes("camera-access")&&g){e.state.unbindTexture(),p=n.getBinding();for(let U=0;U<Le.length;U++){let D=Le[U].camera;if(D){let $=y[D];$||($=new Pa,y[D]=$);let B=p.getCameraImage(D);$.sourceTexture=B}}}}for(let Le=0;Le<R.length;Le++){let Ye=P[Le],We=R[Le];Ye!==null&&We!==void 0&&We.update(Ye,Ee,l||a)}Ae&&Ae(ve,Ee),Ee.detectedPlanes&&n.dispatchEvent({type:"planesdetected",data:Ee}),v=null}),this.setAnimationLoop=function(ve){Ae=ve},this.dispose=function(){}}},Hr=new Wn,Sg=new st;function Tg(r,e){function t(i,s){i.matrixAutoUpdate===!0&&i.updateMatrix(),s.value.copy(i.matrix)}function n(i,s){i.opacity.value=s.opacity,s.color&&i.diffuse.value.copy(s.color),s.emissive&&i.emissive.value.copy(s.emissive).multiplyScalar(s.emissiveIntensity),s.map&&(i.map.value=s.map,t(s.map,i.mapTransform)),s.alphaMap&&(i.alphaMap.value=s.alphaMap,t(s.alphaMap,i.alphaMapTransform)),s.bumpMap&&(i.bumpMap.value=s.bumpMap,t(s.bumpMap,i.bumpMapTransform),i.bumpScale.value=s.bumpScale,s.side===In&&(i.bumpScale.value*=-1)),s.normalMap&&(i.normalMap.value=s.normalMap,t(s.normalMap,i.normalMapTransform),i.normalScale.value.copy(s.normalScale),s.side===In&&i.normalScale.value.negate()),s.displacementMap&&(i.displacementMap.value=s.displacementMap,t(s.displacementMap,i.displacementMapTransform),i.displacementScale.value=s.displacementScale,i.displacementBias.value=s.displacementBias),s.emissiveMap&&(i.emissiveMap.value=s.emissiveMap,t(s.emissiveMap,i.emissiveMapTransform)),s.specularMap&&(i.specularMap.value=s.specularMap,t(s.specularMap,i.specularMapTransform)),s.alphaTest>0&&(i.alphaTest.value=s.alphaTest);let a=e.get(s),o=a.envMap,c=a.envMapRotation;o&&(i.envMap.value=o,Hr.copy(c),Hr.x*=-1,Hr.y*=-1,Hr.z*=-1,o.isCubeTexture&&o.isRenderTargetTexture===!1&&(Hr.y*=-1,Hr.z*=-1),i.envMapRotation.value.setFromMatrix4(Sg.makeRotationFromEuler(Hr)),i.flipEnvMap.value=o.isCubeTexture&&o.isRenderTargetTexture===!1?-1:1,i.reflectivity.value=s.reflectivity,i.ior.value=s.ior,i.refractionRatio.value=s.refractionRatio),s.lightMap&&(i.lightMap.value=s.lightMap,i.lightMapIntensity.value=s.lightMapIntensity,t(s.lightMap,i.lightMapTransform)),s.aoMap&&(i.aoMap.value=s.aoMap,i.aoMapIntensity.value=s.aoMapIntensity,t(s.aoMap,i.aoMapTransform))}return{refreshFogUniforms:function(i,s){s.color.getRGB(i.fogColor.value,Ou(r)),s.isFog?(i.fogNear.value=s.near,i.fogFar.value=s.far):s.isFogExp2&&(i.fogDensity.value=s.density)},refreshMaterialUniforms:function(i,s,a,o,c){s.isMeshBasicMaterial?n(i,s):s.isMeshLambertMaterial?(n(i,s),s.envMap&&(i.envMapIntensity.value=s.envMapIntensity)):s.isMeshToonMaterial?(n(i,s),function(l,h){h.gradientMap&&(l.gradientMap.value=h.gradientMap)}(i,s)):s.isMeshPhongMaterial?(n(i,s),function(l,h){l.specular.value.copy(h.specular),l.shininess.value=Math.max(h.shininess,1e-4)}(i,s),s.envMap&&(i.envMapIntensity.value=s.envMapIntensity)):s.isMeshStandardMaterial?(n(i,s),function(l,h){l.metalness.value=h.metalness,h.metalnessMap&&(l.metalnessMap.value=h.metalnessMap,t(h.metalnessMap,l.metalnessMapTransform)),l.roughness.value=h.roughness,h.roughnessMap&&(l.roughnessMap.value=h.roughnessMap,t(h.roughnessMap,l.roughnessMapTransform)),h.envMap&&(l.envMapIntensity.value=h.envMapIntensity)}(i,s),s.isMeshPhysicalMaterial&&function(l,h,p){l.ior.value=h.ior,h.sheen>0&&(l.sheenColor.value.copy(h.sheenColor).multiplyScalar(h.sheen),l.sheenRoughness.value=h.sheenRoughness,h.sheenColorMap&&(l.sheenColorMap.value=h.sheenColorMap,t(h.sheenColorMap,l.sheenColorMapTransform)),h.sheenRoughnessMap&&(l.sheenRoughnessMap.value=h.sheenRoughnessMap,t(h.sheenRoughnessMap,l.sheenRoughnessMapTransform))),h.clearcoat>0&&(l.clearcoat.value=h.clearcoat,l.clearcoatRoughness.value=h.clearcoatRoughness,h.clearcoatMap&&(l.clearcoatMap.value=h.clearcoatMap,t(h.clearcoatMap,l.clearcoatMapTransform)),h.clearcoatRoughnessMap&&(l.clearcoatRoughnessMap.value=h.clearcoatRoughnessMap,t(h.clearcoatRoughnessMap,l.clearcoatRoughnessMapTransform)),h.clearcoatNormalMap&&(l.clearcoatNormalMap.value=h.clearcoatNormalMap,t(h.clearcoatNormalMap,l.clearcoatNormalMapTransform),l.clearcoatNormalScale.value.copy(h.clearcoatNormalScale),h.side===In&&l.clearcoatNormalScale.value.negate())),h.dispersion>0&&(l.dispersion.value=h.dispersion),h.iridescence>0&&(l.iridescence.value=h.iridescence,l.iridescenceIOR.value=h.iridescenceIOR,l.iridescenceThicknessMinimum.value=h.iridescenceThicknessRange[0],l.iridescenceThicknessMaximum.value=h.iridescenceThicknessRange[1],h.iridescenceMap&&(l.iridescenceMap.value=h.iridescenceMap,t(h.iridescenceMap,l.iridescenceMapTransform)),h.iridescenceThicknessMap&&(l.iridescenceThicknessMap.value=h.iridescenceThicknessMap,t(h.iridescenceThicknessMap,l.iridescenceThicknessMapTransform))),h.transmission>0&&(l.transmission.value=h.transmission,l.transmissionSamplerMap.value=p.texture,l.transmissionSamplerSize.value.set(p.width,p.height),h.transmissionMap&&(l.transmissionMap.value=h.transmissionMap,t(h.transmissionMap,l.transmissionMapTransform)),l.thickness.value=h.thickness,h.thicknessMap&&(l.thicknessMap.value=h.thicknessMap,t(h.thicknessMap,l.thicknessMapTransform)),l.attenuationDistance.value=h.attenuationDistance,l.attenuationColor.value.copy(h.attenuationColor)),h.anisotropy>0&&(l.anisotropyVector.value.set(h.anisotropy*Math.cos(h.anisotropyRotation),h.anisotropy*Math.sin(h.anisotropyRotation)),h.anisotropyMap&&(l.anisotropyMap.value=h.anisotropyMap,t(h.anisotropyMap,l.anisotropyMapTransform))),l.specularIntensity.value=h.specularIntensity,l.specularColor.value.copy(h.specularColor),h.specularColorMap&&(l.specularColorMap.value=h.specularColorMap,t(h.specularColorMap,l.specularColorMapTransform)),h.specularIntensityMap&&(l.specularIntensityMap.value=h.specularIntensityMap,t(h.specularIntensityMap,l.specularIntensityMapTransform))}(i,s,c)):s.isMeshMatcapMaterial?(n(i,s),function(l,h){h.matcap&&(l.matcap.value=h.matcap)}(i,s)):s.isMeshDepthMaterial?n(i,s):s.isMeshDistanceMaterial?(n(i,s),function(l,h){let p=e.get(h).light;l.referencePosition.value.setFromMatrixPosition(p.matrixWorld),l.nearDistance.value=p.shadow.camera.near,l.farDistance.value=p.shadow.camera.far}(i,s)):s.isMeshNormalMaterial?n(i,s):s.isLineBasicMaterial?(function(l,h){l.diffuse.value.copy(h.color),l.opacity.value=h.opacity,h.map&&(l.map.value=h.map,t(h.map,l.mapTransform))}(i,s),s.isLineDashedMaterial&&function(l,h){l.dashSize.value=h.dashSize,l.totalSize.value=h.dashSize+h.gapSize,l.scale.value=h.scale}(i,s)):s.isPointsMaterial?function(l,h,p,f){l.diffuse.value.copy(h.color),l.opacity.value=h.opacity,l.size.value=h.size*p,l.scale.value=.5*f,h.map&&(l.map.value=h.map,t(h.map,l.uvTransform)),h.alphaMap&&(l.alphaMap.value=h.alphaMap,t(h.alphaMap,l.alphaMapTransform)),h.alphaTest>0&&(l.alphaTest.value=h.alphaTest)}(i,s,a,o):s.isSpriteMaterial?function(l,h){l.diffuse.value.copy(h.color),l.opacity.value=h.opacity,l.rotation.value=h.rotation,h.map&&(l.map.value=h.map,t(h.map,l.mapTransform)),h.alphaMap&&(l.alphaMap.value=h.alphaMap,t(h.alphaMap,l.alphaMapTransform)),h.alphaTest>0&&(l.alphaTest.value=h.alphaTest)}(i,s):s.isShadowMaterial?(i.color.value.copy(s.color),i.opacity.value=s.opacity):s.isShaderMaterial&&(s.uniformsNeedUpdate=!1)}}}function Eg(r,e,t,n){let i={},s={},a=[],o=r.getParameter(r.MAX_UNIFORM_BUFFER_BINDINGS);function c(p,f,m,v){let g=p.value,u=f+"_"+m;if(v[u]===void 0)return v[u]=typeof g=="number"||typeof g=="boolean"?g:g.clone(),!0;{let y=v[u];if(typeof g=="number"||typeof g=="boolean"){if(y!==g)return v[u]=g,!0}else if(y.equals(g)===!1)return y.copy(g),!0}return!1}function l(p){let f={boundary:0,storage:0};return typeof p=="number"||typeof p=="boolean"?(f.boundary=4,f.storage=4):p.isVector2?(f.boundary=8,f.storage=8):p.isVector3||p.isColor?(f.boundary=16,f.storage=12):p.isVector4?(f.boundary=16,f.storage=16):p.isMatrix3?(f.boundary=48,f.storage=48):p.isMatrix4?(f.boundary=64,f.storage=64):p.isTexture?tt("WebGLRenderer: Texture samplers can not be part of an uniforms group."):tt("WebGLRenderer: Unsupported uniform value type.",p),f}function h(p){let f=p.target;f.removeEventListener("dispose",h);let m=a.indexOf(f.__bindingPointIndex);a.splice(m,1),r.deleteBuffer(i[f.id]),delete i[f.id],delete s[f.id]}return{bind:function(p,f){let m=f.program;n.uniformBlockBinding(p,m)},update:function(p,f){let m=i[p.id];m===void 0&&(function(u){let y=u.uniforms,x=0,S=16;for(let R=0,P=y.length;R<P;R++){let V=Array.isArray(y[R])?y[R]:[y[R]];for(let X=0,H=V.length;X<H;X++){let se=V[X],oe=Array.isArray(se.value)?se.value:[se.value];for(let te=0,ne=oe.length;te<ne;te++){let j=l(oe[te]),J=x%S,ie=J%j.boundary,re=J+ie;x+=ie,re!==0&&S-re<j.storage&&(x+=S-re),se.__data=new Float32Array(j.storage/Float32Array.BYTES_PER_ELEMENT),se.__offset=x,x+=j.storage}}}let E=x%S;E>0&&(x+=S-E),u.__size=x,u.__cache={}}(p),m=function(u){let y=function(){for(let R=0;R<o;R++)if(a.indexOf(R)===-1)return a.push(R),R;return rt("WebGLRenderer: Maximum number of simultaneously usable uniforms groups reached."),0}();u.__bindingPointIndex=y;let x=r.createBuffer(),S=u.__size,E=u.usage;return r.bindBuffer(r.UNIFORM_BUFFER,x),r.bufferData(r.UNIFORM_BUFFER,S,E),r.bindBuffer(r.UNIFORM_BUFFER,null),r.bindBufferBase(r.UNIFORM_BUFFER,y,x),x}(p),i[p.id]=m,p.addEventListener("dispose",h));let v=f.program;n.updateUBOMapping(p,v);let g=e.render.frame;s[p.id]!==g&&(function(u){let y=i[u.id],x=u.uniforms,S=u.__cache;r.bindBuffer(r.UNIFORM_BUFFER,y);for(let E=0,R=x.length;E<R;E++){let P=Array.isArray(x[E])?x[E]:[x[E]];for(let V=0,X=P.length;V<X;V++){let H=P[V];if(c(H,E,V,S)===!0){let se=H.__offset,oe=Array.isArray(H.value)?H.value:[H.value],te=0;for(let ne=0;ne<oe.length;ne++){let j=oe[ne],J=l(j);typeof j=="number"||typeof j=="boolean"?(H.__data[0]=j,r.bufferSubData(r.UNIFORM_BUFFER,se+te,H.__data)):j.isMatrix3?(H.__data[0]=j.elements[0],H.__data[1]=j.elements[1],H.__data[2]=j.elements[2],H.__data[3]=0,H.__data[4]=j.elements[3],H.__data[5]=j.elements[4],H.__data[6]=j.elements[5],H.__data[7]=0,H.__data[8]=j.elements[6],H.__data[9]=j.elements[7],H.__data[10]=j.elements[8],H.__data[11]=0):(j.toArray(H.__data,te),te+=J.storage/Float32Array.BYTES_PER_ELEMENT)}r.bufferSubData(r.UNIFORM_BUFFER,se,H.__data)}}}r.bindBuffer(r.UNIFORM_BUFFER,null)}(p),s[p.id]=g)},dispose:function(){for(let p in i)r.deleteBuffer(i[p]);a=[],i={},s={}}}}var wg=new Uint16Array([12469,15057,12620,14925,13266,14620,13807,14376,14323,13990,14545,13625,14713,13328,14840,12882,14931,12528,14996,12233,15039,11829,15066,11525,15080,11295,15085,10976,15082,10705,15073,10495,13880,14564,13898,14542,13977,14430,14158,14124,14393,13732,14556,13410,14702,12996,14814,12596,14891,12291,14937,11834,14957,11489,14958,11194,14943,10803,14921,10506,14893,10278,14858,9960,14484,14039,14487,14025,14499,13941,14524,13740,14574,13468,14654,13106,14743,12678,14818,12344,14867,11893,14889,11509,14893,11180,14881,10751,14852,10428,14812,10128,14765,9754,14712,9466,14764,13480,14764,13475,14766,13440,14766,13347,14769,13070,14786,12713,14816,12387,14844,11957,14860,11549,14868,11215,14855,10751,14825,10403,14782,10044,14729,9651,14666,9352,14599,9029,14967,12835,14966,12831,14963,12804,14954,12723,14936,12564,14917,12347,14900,11958,14886,11569,14878,11247,14859,10765,14828,10401,14784,10011,14727,9600,14660,9289,14586,8893,14508,8533,15111,12234,15110,12234,15104,12216,15092,12156,15067,12010,15028,11776,14981,11500,14942,11205,14902,10752,14861,10393,14812,9991,14752,9570,14682,9252,14603,8808,14519,8445,14431,8145,15209,11449,15208,11451,15202,11451,15190,11438,15163,11384,15117,11274,15055,10979,14994,10648,14932,10343,14871,9936,14803,9532,14729,9218,14645,8742,14556,8381,14461,8020,14365,7603,15273,10603,15272,10607,15267,10619,15256,10631,15231,10614,15182,10535,15118,10389,15042,10167,14963,9787,14883,9447,14800,9115,14710,8665,14615,8318,14514,7911,14411,7507,14279,7198,15314,9675,15313,9683,15309,9712,15298,9759,15277,9797,15229,9773,15166,9668,15084,9487,14995,9274,14898,8910,14800,8539,14697,8234,14590,7790,14479,7409,14367,7067,14178,6621,15337,8619,15337,8631,15333,8677,15325,8769,15305,8871,15264,8940,15202,8909,15119,8775,15022,8565,14916,8328,14804,8009,14688,7614,14569,7287,14448,6888,14321,6483,14088,6171,15350,7402,15350,7419,15347,7480,15340,7613,15322,7804,15287,7973,15229,8057,15148,8012,15046,7846,14933,7611,14810,7357,14682,7069,14552,6656,14421,6316,14251,5948,14007,5528,15356,5942,15356,5977,15353,6119,15348,6294,15332,6551,15302,6824,15249,7044,15171,7122,15070,7050,14949,6861,14818,6611,14679,6349,14538,6067,14398,5651,14189,5311,13935,4958,15359,4123,15359,4153,15356,4296,15353,4646,15338,5160,15311,5508,15263,5829,15188,6042,15088,6094,14966,6001,14826,5796,14678,5543,14527,5287,14377,4985,14133,4586,13869,4257,15360,1563,15360,1642,15358,2076,15354,2636,15341,3350,15317,4019,15273,4429,15203,4732,15105,4911,14981,4932,14836,4818,14679,4621,14517,4386,14359,4156,14083,3795,13808,3437,15360,122,15360,137,15358,285,15355,636,15344,1274,15322,2177,15281,2765,15215,3223,15120,3451,14995,3569,14846,3567,14681,3466,14511,3305,14344,3121,14037,2800,13753,2467,15360,0,15360,1,15359,21,15355,89,15346,253,15325,479,15287,796,15225,1148,15133,1492,15008,1749,14856,1882,14685,1886,14506,1783,14324,1608,13996,1398,13702,1183]),Ai=null,vc=class{constructor(e={}){let{canvas:t=Zp(),context:n=null,depth:i=!0,stencil:s=!1,alpha:a=!1,antialias:o=!1,premultipliedAlpha:c=!0,preserveDrawingBuffer:l=!1,powerPreference:h="default",failIfMajorPerformanceCaveat:p=!1,reversedDepthBuffer:f=!1,outputBufferType:m=Yn}=e,v;if(this.isWebGLRenderer=!0,n!==null){if(typeof WebGLRenderingContext<"u"&&n instanceof WebGLRenderingContext)throw new Error("THREE.WebGLRenderer: WebGL 1 is not supported since r163.");v=n.getContextAttributes().alpha}else v=a;let g=m,u=new Set([eu,Qh,sc]),y=new Set([Yn,qi,Bs,zs,nc,ic]),x=new Uint32Array(4),S=new Int32Array(4),E=null,R=null,P=[],V=[],X=null;this.domElement=t,this.debug={checkShaderErrors:!0,onShaderError:null},this.autoClear=!0,this.autoClearColor=!0,this.autoClearDepth=!0,this.autoClearStencil=!0,this.sortObjects=!0,this.clippingPlanes=[],this.localClippingEnabled=!1,this.toneMapping=pi,this.toneMappingExposure=1,this.transmissionResolutionScale=1;let H=this,se=!1;this._outputColorSpace=Xt;let oe=0,te=0,ne=null,j=-1,J=null,ie=new Lt,re=new Lt,Se=null,we=new ke(0),ze=0,Ae=t.width,Ie=t.height,ve=1,Ee=null,Le=null,Ye=new Lt(0,0,Ae,Ie),We=new Lt(0,0,Ae,Ie),U=!1,D=new or,$=!1,B=!1,w=new st,k=new N,Y=new Lt,K={background:null,fog:null,environment:null,overrideMaterial:null,isScene:!0},T=!1;function G(){return ne===null?ve:1}let L,C,z,O,W,Q,xe,fe,Pe,Be,he,me,M,ge,ee,De,Ce,Ke,Je,je,Ne,nt,yt,q=n;function qe(F,ce){return t.getContext(F,ce)}try{let F={alpha:!0,depth:i,stencil:s,antialias:o,premultipliedAlpha:c,preserveDrawingBuffer:l,powerPreference:h,failIfMajorPerformanceCaveat:p};if("setAttribute"in t&&t.setAttribute("data-engine",`three.js r${nl}`),t.addEventListener("webglcontextlost",wn,!1),t.addEventListener("webglcontextrestored",Dn,!1),t.addEventListener("webglcontextcreationerror",Zn,!1),q===null){let ce="webgl2";if(q=qe(ce,F),q===null)throw qe(ce)?new Error("Error creating WebGL context with your selected attributes."):new Error("Error creating WebGL context.")}}catch(F){throw rt("WebGLRenderer: "+F.message),F}function Oe(){L=new Qm(q),L.init(),Ne=new bg(q,L),C=new Zm(q,L,e,Ne),z=new xg(q,L),C.reversedDepthBuffer&&f&&z.buffers.depth.setReversed(!0),O=new n0(q),W=new cg,Q=new Mg(q,L,z,W,C,Ne,O),xe=new $m(H),fe=new Wm(q),nt=new qm(q,fe),Pe=new e0(q,fe,O,nt),Be=new r0(q,Pe,fe,nt,O),Ke=new i0(q,C,Q),ee=new Jm(W),he=new lg(H,xe,L,C,nt,ee),me=new Tg(H,W),M=new ug,ge=new gg(L),Ce=new Xm(H,xe,z,Be,v,c),De=new yg(H,Be,C),yt=new Eg(q,O,C,z),Je=new Ym(q,L,O),je=new t0(q,L,O),O.programs=he.programs,H.capabilities=C,H.extensions=L,H.properties=W,H.renderLists=M,H.shadowMap=De,H.state=z,H.info=O}Oe(),g!==Yn&&(X=new a0(g,t.width,t.height,i,s));let Qe=new Qu(H,q);function wn(F){F.preventDefault(),Uu("WebGLRenderer: Context Lost."),se=!0}function Dn(){Uu("WebGLRenderer: Context Restored."),se=!1;let F=O.autoReset,ce=De.enabled,de=De.autoUpdate,be=De.needsUpdate,_e=De.type;Oe(),O.autoReset=F,De.enabled=ce,De.autoUpdate=de,De.needsUpdate=be,De.type=_e}function Zn(F){rt("WebGLRenderer: A WebGL context could not be created. Reason: ",F.statusMessage)}function gr(F){let ce=F.target;ce.removeEventListener("dispose",gr),function(de){(function(be){let _e=W.get(be).programs;_e!==void 0&&(_e.forEach(function(Fe){he.releaseProgram(Fe)}),be.isShaderMaterial&&he.releaseShaderCache(be))})(de),W.remove(de)}(ce)}function Vt(F,ce,de){F.transparent===!0&&F.side===En&&F.forceSinglePass===!1?(F.side=In,F.needsUpdate=!0,Yi(F,ce,de),F.side=Ns,F.needsUpdate=!0,Yi(F,ce,de),F.side=En):Yi(F,ce,de)}this.xr=Qe,this.getContext=function(){return q},this.getContextAttributes=function(){return q.getContextAttributes()},this.forceContextLoss=function(){let F=L.get("WEBGL_lose_context");F&&F.loseContext()},this.forceContextRestore=function(){let F=L.get("WEBGL_lose_context");F&&F.restoreContext()},this.getPixelRatio=function(){return ve},this.setPixelRatio=function(F){F!==void 0&&(ve=F,this.setSize(Ae,Ie,!1))},this.getSize=function(F){return F.set(Ae,Ie)},this.setSize=function(F,ce,de=!0){Qe.isPresenting?tt("WebGLRenderer: Can't change size while VR device is presenting."):(Ae=F,Ie=ce,t.width=Math.floor(F*ve),t.height=Math.floor(ce*ve),de===!0&&(t.style.width=F+"px",t.style.height=ce+"px"),X!==null&&X.setSize(t.width,t.height),this.setViewport(0,0,F,ce))},this.getDrawingBufferSize=function(F){return F.set(Ae*ve,Ie*ve).floor()},this.setDrawingBufferSize=function(F,ce,de){Ae=F,Ie=ce,ve=de,t.width=Math.floor(F*de),t.height=Math.floor(ce*de),this.setViewport(0,0,F,ce)},this.setEffects=function(F){if(g!==Yn){if(F){for(let ce=0;ce<F.length;ce++)if(F[ce].isOutputPass===!0){console.warn("THREE.WebGLRenderer: OutputPass is not needed in setEffects(). Tone mapping and color space conversion are applied automatically.");break}}X.setEffects(F||[])}else console.error("THREE.WebGLRenderer: setEffects() requires outputBufferType set to HalfFloatType or FloatType.")},this.getCurrentViewport=function(F){return F.copy(ie)},this.getViewport=function(F){return F.copy(Ye)},this.setViewport=function(F,ce,de,be){F.isVector4?Ye.set(F.x,F.y,F.z,F.w):Ye.set(F,ce,de,be),z.viewport(ie.copy(Ye).multiplyScalar(ve).round())},this.getScissor=function(F){return F.copy(We)},this.setScissor=function(F,ce,de,be){F.isVector4?We.set(F.x,F.y,F.z,F.w):We.set(F,ce,de,be),z.scissor(re.copy(We).multiplyScalar(ve).round())},this.getScissorTest=function(){return U},this.setScissorTest=function(F){z.setScissorTest(U=F)},this.setOpaqueSort=function(F){Ee=F},this.setTransparentSort=function(F){Le=F},this.getClearColor=function(F){return F.copy(Ce.getClearColor())},this.setClearColor=function(){Ce.setClearColor(...arguments)},this.getClearAlpha=function(){return Ce.getClearAlpha()},this.setClearAlpha=function(){Ce.setClearAlpha(...arguments)},this.clear=function(F=!0,ce=!0,de=!0){let be=0;if(F){let _e=!1;if(ne!==null){let Fe=ne.texture.format;_e=u.has(Fe)}if(_e){let Fe=ne.texture.type,He=y.has(Fe),Xe=Ce.getClearColor(),Ve=Ce.getClearAlpha(),it=Xe.r,dt=Xe.g,_t=Xe.b;He?(x[0]=it,x[1]=dt,x[2]=_t,x[3]=Ve,q.clearBufferuiv(q.COLOR,0,x)):(S[0]=it,S[1]=dt,S[2]=_t,S[3]=Ve,q.clearBufferiv(q.COLOR,0,S))}else be|=q.COLOR_BUFFER_BIT}ce&&(be|=q.DEPTH_BUFFER_BIT),de&&(be|=q.STENCIL_BUFFER_BIT,this.state.buffers.stencil.setMask(4294967295)),be!==0&&q.clear(be)},this.clearColor=function(){this.clear(!0,!1,!1)},this.clearDepth=function(){this.clear(!1,!0,!1)},this.clearStencil=function(){this.clear(!1,!1,!0)},this.dispose=function(){t.removeEventListener("webglcontextlost",wn,!1),t.removeEventListener("webglcontextrestored",Dn,!1),t.removeEventListener("webglcontextcreationerror",Zn,!1),Ce.dispose(),M.dispose(),ge.dispose(),W.dispose(),xe.dispose(),Be.dispose(),nt.dispose(),yt.dispose(),he.dispose(),Qe.dispose(),Qe.removeEventListener("sessionstart",ro),Qe.removeEventListener("sessionend",so),rn.stop()},this.renderBufferDirect=function(F,ce,de,be,_e,Fe){ce===null&&(ce=K);let He=_e.isMesh&&_e.matrixWorld.determinant()<0,Xe=function(mt,At,Ht,at,lt){At.isScene!==!0&&(At=K),Q.resetTextureUnits();let xn=At.fog,xr=at.isMeshStandardMaterial||at.isMeshLambertMaterial||at.isMeshPhongMaterial?At.environment:null,Zi=ne===null?H.outputColorSpace:ne.isXRRenderTarget===!0?ne.texture.colorSpace:Rr,ai=at.isMeshStandardMaterial||at.isMeshLambertMaterial&&!at.envMap||at.isMeshPhongMaterial&&!at.envMap,dn=xe.get(at.envMap||xr,ai),Un=at.vertexColors===!0&&!!Ht.attributes.color&&Ht.attributes.color.itemSize===4,zt=!!Ht.attributes.tangent&&(!!at.normalMap||at.anisotropy>0),Nn=!!Ht.morphAttributes.position,pn=!!Ht.morphAttributes.normal,An=!!Ht.morphAttributes.color,oi=pi;at.toneMapped&&(ne!==null&&ne.isXRRenderTarget!==!0||(oi=H.toneMapping));let Mr=Ht.morphAttributes.position||Ht.morphAttributes.normal||Ht.morphAttributes.color,Mn=Mr!==void 0?Mr.length:0,Ze=W.get(at),vt=R.state.lights;if($===!0&&(B===!0||mt!==J)){let ot=mt===J&&at.id===j;ee.setState(at,mt,ot)}let Ot=!1;at.version===Ze.__version?Ze.needsLights&&Ze.lightsStateVersion!==vt.state.version||Ze.outputColorSpace!==Zi||lt.isBatchedMesh&&Ze.batching===!1?Ot=!0:lt.isBatchedMesh||Ze.batching!==!0?lt.isBatchedMesh&&Ze.batchingColor===!0&&lt.colorTexture===null||lt.isBatchedMesh&&Ze.batchingColor===!1&&lt.colorTexture!==null||lt.isInstancedMesh&&Ze.instancing===!1?Ot=!0:lt.isInstancedMesh||Ze.instancing!==!0?lt.isSkinnedMesh&&Ze.skinning===!1?Ot=!0:lt.isSkinnedMesh||Ze.skinning!==!0?lt.isInstancedMesh&&Ze.instancingColor===!0&&lt.instanceColor===null||lt.isInstancedMesh&&Ze.instancingColor===!1&&lt.instanceColor!==null||lt.isInstancedMesh&&Ze.instancingMorph===!0&&lt.morphTexture===null||lt.isInstancedMesh&&Ze.instancingMorph===!1&&lt.morphTexture!==null||Ze.envMap!==dn||at.fog===!0&&Ze.fog!==xn?Ot=!0:Ze.numClippingPlanes===void 0||Ze.numClippingPlanes===ee.numPlanes&&Ze.numIntersection===ee.numIntersection?(Ze.vertexAlphas!==Un||Ze.vertexTangents!==zt||Ze.morphTargets!==Nn||Ze.morphNormals!==pn||Ze.morphColors!==An||Ze.toneMapping!==oi||Ze.morphTargetsCount!==Mn)&&(Ot=!0):Ot=!0:Ot=!0:Ot=!0:Ot=!0:(Ot=!0,Ze.__version=at.version);let On=Ze.currentProgram;Ot===!0&&(On=Yi(at,At,lt));let xt=!1,Ii=!1,Jn=!1,Tt=On.getUniforms(),Cn=Ze.uniforms;if(z.useProgram(On.program)&&(xt=!0,Ii=!0,Jn=!0),at.id!==j&&(j=at.id,Ii=!0),xt||J!==mt){z.buffers.depth.getReversed()&&mt.reversedDepth!==!0&&(mt._reversedDepth=!0,mt.updateProjectionMatrix()),Tt.setValue(q,"projectionMatrix",mt.projectionMatrix),Tt.setValue(q,"viewMatrix",mt.matrixWorldInverse);let ot=Tt.map.cameraPosition;ot!==void 0&&ot.setValue(q,k.setFromMatrixPosition(mt.matrixWorld)),C.logarithmicDepthBuffer&&Tt.setValue(q,"logDepthBufFC",2/(Math.log(mt.far+1)/Math.LN2)),(at.isMeshPhongMaterial||at.isMeshToonMaterial||at.isMeshLambertMaterial||at.isMeshBasicMaterial||at.isMeshStandardMaterial||at.isShaderMaterial)&&Tt.setValue(q,"isOrthographic",mt.isOrthographicCamera===!0),J!==mt&&(J=mt,Ii=!0,Jn=!0)}if(Ze.needsLights&&(vt.state.directionalShadowMap.length>0&&Tt.setValue(q,"directionalShadowMap",vt.state.directionalShadowMap,Q),vt.state.spotShadowMap.length>0&&Tt.setValue(q,"spotShadowMap",vt.state.spotShadowMap,Q),vt.state.pointShadowMap.length>0&&Tt.setValue(q,"pointShadowMap",vt.state.pointShadowMap,Q)),lt.isSkinnedMesh){Tt.setOptional(q,lt,"bindMatrix"),Tt.setOptional(q,lt,"bindMatrixInverse");let ot=lt.skeleton;ot&&(ot.boneTexture===null&&ot.computeBoneTexture(),Tt.setValue(q,"boneTexture",ot.boneTexture,Q))}lt.isBatchedMesh&&(Tt.setOptional(q,lt,"batchingTexture"),Tt.setValue(q,"batchingTexture",lt._matricesTexture,Q),Tt.setOptional(q,lt,"batchingIdTexture"),Tt.setValue(q,"batchingIdTexture",lt._indirectTexture,Q),Tt.setOptional(q,lt,"batchingColorTexture"),lt._colorsTexture!==null&&Tt.setValue(q,"batchingColorTexture",lt._colorsTexture,Q));let Li=Ht.morphAttributes;Li.position===void 0&&Li.normal===void 0&&Li.color===void 0||Ke.update(lt,Ht,On),(Ii||Ze.receiveShadow!==lt.receiveShadow)&&(Ze.receiveShadow=lt.receiveShadow,Tt.setValue(q,"receiveShadow",lt.receiveShadow)),(at.isMeshStandardMaterial||at.isMeshLambertMaterial||at.isMeshPhongMaterial)&&at.envMap===null&&At.environment!==null&&(Cn.envMapIntensity.value=At.environmentIntensity),Cn.dfgLUT!==void 0&&(Cn.dfgLUT.value=(Ai===null&&(Ai=new Ea(wg,16,16,ks,wi),Ai.name="DFG_LUT",Ai.minFilter=nn,Ai.magFilter=nn,Ai.wrapS=ar,Ai.wrapT=ar,Ai.generateMipmaps=!1,Ai.needsUpdate=!0),Ai)),Ii&&(Tt.setValue(q,"toneMappingExposure",H.toneMappingExposure),Ze.needsLights&&(Yt=Jn,(qt=Cn).ambientLightColor.needsUpdate=Yt,qt.lightProbe.needsUpdate=Yt,qt.directionalLights.needsUpdate=Yt,qt.directionalLightShadows.needsUpdate=Yt,qt.pointLights.needsUpdate=Yt,qt.pointLightShadows.needsUpdate=Yt,qt.spotLights.needsUpdate=Yt,qt.spotLightShadows.needsUpdate=Yt,qt.rectAreaLights.needsUpdate=Yt,qt.hemisphereLights.needsUpdate=Yt),xn&&at.fog===!0&&me.refreshFogUniforms(Cn,xn),me.refreshMaterialUniforms(Cn,at,ve,Ie,R.state.transmissionRenderTarget[mt.id]),Gs.upload(q,Vr(Ze),Cn,Q));var qt,Yt;if(at.isShaderMaterial&&at.uniformsNeedUpdate===!0&&(Gs.upload(q,Vr(Ze),Cn,Q),at.uniformsNeedUpdate=!1),at.isSpriteMaterial&&Tt.setValue(q,"center",lt.center),Tt.setValue(q,"modelViewMatrix",lt.modelViewMatrix),Tt.setValue(q,"normalMatrix",lt.normalMatrix),Tt.setValue(q,"modelMatrix",lt.matrixWorld),at.isShaderMaterial||at.isRawShaderMaterial){let ot=at.uniformsGroups;for(let Wt=0,mi=ot.length;Wt<mi;Wt++){let Di=ot[Wt];yt.update(Di,On),yt.bind(Di,On)}}return On}(F,ce,de,be,_e);z.setMaterial(be,He);let Ve=de.index,it=1;if(be.wireframe===!0){if(Ve=Pe.getWireframeAttribute(de),Ve===void 0)return;it=2}let dt=de.drawRange,_t=de.attributes.position,et=dt.start*it,ft=(dt.start+dt.count)*it;Fe!==null&&(et=Math.max(et,Fe.start*it),ft=Math.min(ft,(Fe.start+Fe.count)*it)),Ve!==null?(et=Math.max(et,0),ft=Math.min(ft,Ve.count)):_t!=null&&(et=Math.max(et,0),ft=Math.min(ft,_t.count));let Dt=ft-et;if(Dt<0||Dt===1/0)return;let Ut;nt.setup(_e,be,Xe,de,Ve);let wt=Je;if(Ve!==null&&(Ut=fe.get(Ve),wt=je,wt.setIndex(Ut)),_e.isMesh)be.wireframe===!0?(z.setLineWidth(be.wireframeLinewidth*G()),wt.setMode(q.LINES)):wt.setMode(q.TRIANGLES);else if(_e.isLine){let mt=be.linewidth;mt===void 0&&(mt=1),z.setLineWidth(mt*G()),_e.isLineSegments?wt.setMode(q.LINES):_e.isLineLoop?wt.setMode(q.LINE_LOOP):wt.setMode(q.LINE_STRIP)}else _e.isPoints?wt.setMode(q.POINTS):_e.isSprite&&wt.setMode(q.TRIANGLES);if(_e.isBatchedMesh)if(_e._multiDrawInstances!==null)ya("WebGLRenderer: renderMultiDrawInstances has been deprecated and will be removed in r184. Append to renderMultiDraw arguments and use indirection."),wt.renderMultiDrawInstances(_e._multiDrawStarts,_e._multiDrawCounts,_e._multiDrawCount,_e._multiDrawInstances);else if(L.get("WEBGL_multi_draw"))wt.renderMultiDraw(_e._multiDrawStarts,_e._multiDrawCounts,_e._multiDrawCount);else{let mt=_e._multiDrawStarts,At=_e._multiDrawCounts,Ht=_e._multiDrawCount,at=Ve?fe.get(Ve).bytesPerElement:1,lt=W.get(be).currentProgram.getUniforms();for(let xn=0;xn<Ht;xn++)lt.setValue(q,"_gl_DrawID",xn),wt.render(mt[xn]/at,At[xn])}else if(_e.isInstancedMesh)wt.renderInstances(et,Dt,_e.count);else if(de.isInstancedBufferGeometry){let mt=de._maxInstanceCount!==void 0?de._maxInstanceCount:1/0,At=Math.min(de.instanceCount,mt);wt.renderInstances(et,Dt,At)}else wt.render(et,Dt)},this.compile=function(F,ce,de=null){de===null&&(de=F),R=ge.get(de),R.init(ce),V.push(R),de.traverseVisible(function(_e){_e.isLight&&_e.layers.test(ce.layers)&&(R.pushLight(_e),_e.castShadow&&R.pushShadow(_e))}),F!==de&&F.traverseVisible(function(_e){_e.isLight&&_e.layers.test(ce.layers)&&(R.pushLight(_e),_e.castShadow&&R.pushShadow(_e))}),R.setupLights();let be=new Set;return F.traverse(function(_e){if(!(_e.isMesh||_e.isPoints||_e.isLine||_e.isSprite))return;let Fe=_e.material;if(Fe)if(Array.isArray(Fe))for(let He=0;He<Fe.length;He++){let Xe=Fe[He];Vt(Xe,de,_e),be.add(Xe)}else Vt(Fe,de,_e),be.add(Fe)}),R=V.pop(),be},this.compileAsync=function(F,ce,de=null){let be=this.compile(F,ce,de);return new Promise(_e=>{function Fe(){be.forEach(function(He){W.get(He).currentProgram.isReady()&&be.delete(He)}),be.size!==0?setTimeout(Fe,10):_e(F)}L.get("KHR_parallel_shader_compile")!==null?Fe():setTimeout(Fe,10)})};let Pt=null;function ro(){rn.stop()}function so(){rn.start()}let rn=new Cf;function _r(F,ce,de,be){if(F.visible===!1)return;if(F.layers.test(ce.layers)){if(F.isGroup)de=F.renderOrder;else if(F.isLOD)F.autoUpdate===!0&&F.update(ce);else if(F.isLight)R.pushLight(F),F.castShadow&&R.pushShadow(F);else if(F.isSprite){if(!F.frustumCulled||D.intersectsSprite(F)){be&&Y.setFromMatrixPosition(F.matrixWorld).applyMatrix4(w);let Fe=Be.update(F),He=F.material;He.visible&&E.push(F,Fe,He,de,Y.z,null)}}else if((F.isMesh||F.isLine||F.isPoints)&&(!F.frustumCulled||D.intersectsObject(F))){let Fe=Be.update(F),He=F.material;if(be&&(F.boundingSphere!==void 0?(F.boundingSphere===null&&F.computeBoundingSphere(),Y.copy(F.boundingSphere.center)):(Fe.boundingSphere===null&&Fe.computeBoundingSphere(),Y.copy(Fe.boundingSphere.center)),Y.applyMatrix4(F.matrixWorld).applyMatrix4(w)),Array.isArray(He)){let Xe=Fe.groups;for(let Ve=0,it=Xe.length;Ve<it;Ve++){let dt=Xe[Ve],_t=He[dt.materialIndex];_t&&_t.visible&&E.push(F,Fe,_t,de,Y.z,dt)}}else He.visible&&E.push(F,Fe,He,de,Y.z,null)}}let _e=F.children;for(let Fe=0,He=_e.length;Fe<He;Fe++)_r(_e[Fe],ce,de,be)}function js(F,ce,de,be){let{opaque:_e,transmissive:Fe,transparent:He}=F;R.setupLightsView(de),$===!0&&ee.setGlobalState(H.clippingPlanes,de),be&&z.viewport(ie.copy(be)),_e.length>0&&un(_e,ce,de),Fe.length>0&&un(Fe,ce,de),He.length>0&&un(He,ce,de),z.buffers.depth.setTest(!0),z.buffers.depth.setMask(!0),z.buffers.color.setMask(!0),z.setPolygonOffset(!1)}function Xs(F,ce,de,be){if((de.isScene===!0?de.overrideMaterial:null)!==null)return;if(R.state.transmissionRenderTarget[be.id]===void 0){let _t=L.has("EXT_color_buffer_half_float")||L.has("EXT_color_buffer_float");R.state.transmissionRenderTarget[be.id]=new Vn(1,1,{generateMipmaps:!0,type:_t?wi:Yn,minFilter:Nr,samples:C.samples,stencilBuffer:s,resolveDepthBuffer:!1,resolveStencilBuffer:!1,colorSpace:Mt.workingColorSpace})}let _e=R.state.transmissionRenderTarget[be.id],Fe=be.viewport||ie;_e.setSize(Fe.z*H.transmissionResolutionScale,Fe.w*H.transmissionResolutionScale);let He=H.getRenderTarget(),Xe=H.getActiveCubeFace(),Ve=H.getActiveMipmapLevel();H.setRenderTarget(_e),H.getClearColor(we),ze=H.getClearAlpha(),ze<1&&H.setClearColor(16777215,.5),H.clear(),T&&Ce.render(de);let it=H.toneMapping;H.toneMapping=pi;let dt=be.viewport;if(be.viewport!==void 0&&(be.viewport=void 0),R.setupLightsView(be),$===!0&&ee.setGlobalState(H.clippingPlanes,be),un(F,de,be),Q.updateMultisampleRenderTarget(_e),Q.updateRenderTargetMipmap(_e),L.has("WEBGL_multisampled_render_to_texture")===!1){let _t=!1;for(let et=0,ft=ce.length;et<ft;et++){let Dt=ce[et],{object:Ut,geometry:wt,material:mt,group:At}=Dt;if(mt.side===En&&Ut.layers.test(be.layers)){let Ht=mt.side;mt.side=In,mt.needsUpdate=!0,Gr(Ut,de,be,wt,mt,At),mt.side=Ht,mt.needsUpdate=!0,_t=!0}}_t===!0&&(Q.updateMultisampleRenderTarget(_e),Q.updateRenderTargetMipmap(_e))}H.setRenderTarget(He,Xe,Ve),H.setClearColor(we,ze),dt!==void 0&&(be.viewport=dt),H.toneMapping=it}function un(F,ce,de){let be=ce.isScene===!0?ce.overrideMaterial:null;for(let _e=0,Fe=F.length;_e<Fe;_e++){let He=F[_e],{object:Xe,geometry:Ve,group:it}=He,dt=He.material;dt.allowOverride===!0&&be!==null&&(dt=be),Xe.layers.test(de.layers)&&Gr(Xe,ce,de,Ve,dt,it)}}function Gr(F,ce,de,be,_e,Fe){F.onBeforeRender(H,ce,de,be,_e,Fe),F.modelViewMatrix.multiplyMatrices(de.matrixWorldInverse,F.matrixWorld),F.normalMatrix.getNormalMatrix(F.modelViewMatrix),_e.onBeforeRender(H,ce,de,be,F,Fe),_e.transparent===!0&&_e.side===En&&_e.forceSinglePass===!1?(_e.side=In,_e.needsUpdate=!0,H.renderBufferDirect(de,ce,be,_e,F,Fe),_e.side=Ns,_e.needsUpdate=!0,H.renderBufferDirect(de,ce,be,_e,F,Fe),_e.side=En):H.renderBufferDirect(de,ce,be,_e,F,Fe),F.onAfterRender(H,ce,de,be,_e,Fe)}function Yi(F,ce,de){ce.isScene!==!0&&(ce=K);let be=W.get(F),_e=R.state.lights,Fe=R.state.shadowsArray,He=_e.state.version,Xe=he.getParameters(F,_e.state,Fe,ce,de),Ve=he.getProgramCacheKey(Xe),it=be.programs;be.environment=F.isMeshStandardMaterial||F.isMeshLambertMaterial||F.isMeshPhongMaterial?ce.environment:null,be.fog=ce.fog;let dt=F.isMeshStandardMaterial||F.isMeshLambertMaterial&&!F.envMap||F.isMeshPhongMaterial&&!F.envMap;be.envMap=xe.get(F.envMap||be.environment,dt),be.envMapRotation=be.environment!==null&&F.envMap===null?ce.environmentRotation:F.envMapRotation,it===void 0&&(F.addEventListener("dispose",gr),it=new Map,be.programs=it);let _t=it.get(Ve);if(_t!==void 0){if(be.currentProgram===_t&&be.lightsStateVersion===He)return vr(F,Xe),_t}else Xe.uniforms=he.getUniforms(F),F.onBeforeCompile(Xe,H),_t=he.acquireProgram(Xe,Ve),it.set(Ve,_t),be.uniforms=Xe.uniforms;let et=be.uniforms;return(F.isShaderMaterial||F.isRawShaderMaterial)&&F.clipping!==!0||(et.clippingPlanes=ee.uniform),vr(F,Xe),be.needsLights=function(ft){return ft.isMeshLambertMaterial||ft.isMeshToonMaterial||ft.isMeshPhongMaterial||ft.isMeshStandardMaterial||ft.isShadowMaterial||ft.isShaderMaterial&&ft.lights===!0}(F),be.lightsStateVersion=He,be.needsLights&&(et.ambientLightColor.value=_e.state.ambient,et.lightProbe.value=_e.state.probe,et.directionalLights.value=_e.state.directional,et.directionalLightShadows.value=_e.state.directionalShadow,et.spotLights.value=_e.state.spot,et.spotLightShadows.value=_e.state.spotShadow,et.rectAreaLights.value=_e.state.rectArea,et.ltc_1.value=_e.state.rectAreaLTC1,et.ltc_2.value=_e.state.rectAreaLTC2,et.pointLights.value=_e.state.point,et.pointLightShadows.value=_e.state.pointShadow,et.hemisphereLights.value=_e.state.hemi,et.directionalShadowMatrix.value=_e.state.directionalShadowMatrix,et.spotLightMatrix.value=_e.state.spotLightMatrix,et.spotLightMap.value=_e.state.spotLightMap,et.pointShadowMatrix.value=_e.state.pointShadowMatrix),be.currentProgram=_t,be.uniformsList=null,_t}function Vr(F){if(F.uniformsList===null){let ce=F.currentProgram.getUniforms();F.uniformsList=Gs.seqWithValue(ce.seq,F.uniforms)}return F.uniformsList}function vr(F,ce){let de=W.get(F);de.outputColorSpace=ce.outputColorSpace,de.batching=ce.batching,de.batchingColor=ce.batchingColor,de.instancing=ce.instancing,de.instancingColor=ce.instancingColor,de.instancingMorph=ce.instancingMorph,de.skinning=ce.skinning,de.morphTargets=ce.morphTargets,de.morphNormals=ce.morphNormals,de.morphColors=ce.morphColors,de.morphTargetsCount=ce.morphTargetsCount,de.numClippingPlanes=ce.numClippingPlanes,de.numIntersection=ce.numClipIntersection,de.vertexAlphas=ce.vertexAlphas,de.vertexTangents=ce.vertexTangents,de.toneMapping=ce.toneMapping}rn.setAnimationLoop(function(F){Pt&&Pt(F)}),typeof self<"u"&&rn.setContext(self),this.setAnimationLoop=function(F){Pt=F,Qe.setAnimationLoop(F),F===null?rn.stop():rn.start()},Qe.addEventListener("sessionstart",ro),Qe.addEventListener("sessionend",so),this.render=function(F,ce){if(ce!==void 0&&ce.isCamera!==!0)return void rt("WebGLRenderer.render: camera is not an instance of THREE.Camera.");if(se===!0)return;let de=Qe.enabled===!0&&Qe.isPresenting===!0,be=X!==null&&(ne===null||de)&&X.begin(H,ne);if(F.matrixWorldAutoUpdate===!0&&F.updateMatrixWorld(),ce.parent===null&&ce.matrixWorldAutoUpdate===!0&&ce.updateMatrixWorld(),Qe.enabled!==!0||Qe.isPresenting!==!0||X!==null&&X.isCompositing()!==!1||(Qe.cameraAutoUpdate===!0&&Qe.updateCamera(ce),ce=Qe.getCamera()),F.isScene===!0&&F.onBeforeRender(H,F,ce,ne),R=ge.get(F,V.length),R.init(ce),V.push(R),w.multiplyMatrices(ce.projectionMatrix,ce.matrixWorldInverse),D.setFromProjectionMatrix(w,Xi,ce.reversedDepth),B=this.localClippingEnabled,$=ee.init(this.clippingPlanes,B),E=M.get(F,P.length),E.init(),P.push(E),Qe.enabled===!0&&Qe.isPresenting===!0){let Fe=H.xr.getDepthSensingMesh();Fe!==null&&_r(Fe,ce,-1/0,H.sortObjects)}_r(F,ce,0,H.sortObjects),E.finish(),H.sortObjects===!0&&E.sort(Ee,Le),T=Qe.enabled===!1||Qe.isPresenting===!1||Qe.hasDepthSensing()===!1,T&&Ce.addToRenderList(E,F),this.info.render.frame++,$===!0&&ee.beginShadows();let _e=R.state.shadowsArray;if(De.render(_e,F,ce),$===!0&&ee.endShadows(),this.info.autoReset===!0&&this.info.reset(),(be&&X.hasRenderPass())===!1){let Fe=E.opaque,He=E.transmissive;if(R.setupLights(),ce.isArrayCamera){let Xe=ce.cameras;if(He.length>0)for(let Ve=0,it=Xe.length;Ve<it;Ve++)Xs(Fe,He,F,Xe[Ve]);T&&Ce.render(F);for(let Ve=0,it=Xe.length;Ve<it;Ve++){let dt=Xe[Ve];js(E,F,dt,dt.viewport)}}else He.length>0&&Xs(Fe,He,F,ce),T&&Ce.render(F),js(E,F,ce)}ne!==null&&te===0&&(Q.updateMultisampleRenderTarget(ne),Q.updateRenderTargetMipmap(ne)),be&&X.end(H),F.isScene===!0&&F.onAfterRender(H,F,ce),nt.resetDefaultState(),j=-1,J=null,V.pop(),V.length>0?(R=V[V.length-1],$===!0&&ee.setGlobalState(H.clippingPlanes,R.state.camera)):R=null,P.pop(),E=P.length>0?P[P.length-1]:null},this.getActiveCubeFace=function(){return oe},this.getActiveMipmapLevel=function(){return te},this.getRenderTarget=function(){return ne},this.setRenderTargetTextures=function(F,ce,de){let be=W.get(F);be.__autoAllocateDepthBuffer=F.resolveDepthBuffer===!1,be.__autoAllocateDepthBuffer===!1&&(be.__useRenderToTexture=!1),W.get(F.texture).__webglTexture=ce,W.get(F.depthTexture).__webglTexture=be.__autoAllocateDepthBuffer?void 0:de,be.__hasExternalTextures=!0},this.setRenderTargetFramebuffer=function(F,ce){let de=W.get(F);de.__webglFramebuffer=ce,de.__useDefaultFramebuffer=ce===void 0};let Ri=q.createFramebuffer();this.setRenderTarget=function(F,ce=0,de=0){ne=F,oe=ce,te=de;let be=null,_e=!1,Fe=!1;if(F){let He=W.get(F);if(He.__useDefaultFramebuffer!==void 0)return z.bindFramebuffer(q.FRAMEBUFFER,He.__webglFramebuffer),ie.copy(F.viewport),re.copy(F.scissor),Se=F.scissorTest,z.viewport(ie),z.scissor(re),z.setScissorTest(Se),void(j=-1);if(He.__webglFramebuffer===void 0)Q.setupRenderTarget(F);else if(He.__hasExternalTextures)Q.rebindTextures(F,W.get(F.texture).__webglTexture,W.get(F.depthTexture).__webglTexture);else if(F.depthBuffer){let it=F.depthTexture;if(He.__boundDepthTexture!==it){if(it!==null&&W.has(it)&&(F.width!==it.image.width||F.height!==it.image.height))throw new Error("WebGLRenderTarget: Attached DepthTexture is initialized to the incorrect size.");Q.setupDepthRenderbuffer(F)}}let Xe=F.texture;(Xe.isData3DTexture||Xe.isDataArrayTexture||Xe.isCompressedArrayTexture)&&(Fe=!0);let Ve=W.get(F).__webglFramebuffer;F.isWebGLCubeRenderTarget?(be=Array.isArray(Ve[ce])?Ve[ce][de]:Ve[ce],_e=!0):be=F.samples>0&&Q.useMultisampledRTT(F)===!1?W.get(F).__webglMultisampledFramebuffer:Array.isArray(Ve)?Ve[de]:Ve,ie.copy(F.viewport),re.copy(F.scissor),Se=F.scissorTest}else ie.copy(Ye).multiplyScalar(ve).floor(),re.copy(We).multiplyScalar(ve).floor(),Se=U;if(de!==0&&(be=Ri),z.bindFramebuffer(q.FRAMEBUFFER,be)&&z.drawBuffers(F,be),z.viewport(ie),z.scissor(re),z.setScissorTest(Se),_e){let He=W.get(F.texture);q.framebufferTexture2D(q.FRAMEBUFFER,q.COLOR_ATTACHMENT0,q.TEXTURE_CUBE_MAP_POSITIVE_X+ce,He.__webglTexture,de)}else if(Fe){let He=ce;for(let Xe=0;Xe<F.textures.length;Xe++){let Ve=W.get(F.textures[Xe]);q.framebufferTextureLayer(q.FRAMEBUFFER,q.COLOR_ATTACHMENT0+Xe,Ve.__webglTexture,de,He)}}else if(F!==null&&de!==0){let He=W.get(F.texture);q.framebufferTexture2D(q.FRAMEBUFFER,q.COLOR_ATTACHMENT0,q.TEXTURE_2D,He.__webglTexture,de)}j=-1},this.readRenderTargetPixels=function(F,ce,de,be,_e,Fe,He,Xe=0){if(!F||!F.isWebGLRenderTarget)return void rt("WebGLRenderer.readRenderTargetPixels: renderTarget is not THREE.WebGLRenderTarget.");let Ve=W.get(F).__webglFramebuffer;if(F.isWebGLCubeRenderTarget&&He!==void 0&&(Ve=Ve[He]),Ve){z.bindFramebuffer(q.FRAMEBUFFER,Ve);try{let it=F.textures[Xe],dt=it.format,_t=it.type;if(F.textures.length>1&&q.readBuffer(q.COLOR_ATTACHMENT0+Xe),!C.textureFormatReadable(dt))return void rt("WebGLRenderer.readRenderTargetPixels: renderTarget is not in RGBA or implementation defined format.");if(!C.textureTypeReadable(_t))return void rt("WebGLRenderer.readRenderTargetPixels: renderTarget is not in UnsignedByteType or implementation defined type.");ce>=0&&ce<=F.width-be&&de>=0&&de<=F.height-_e&&q.readPixels(ce,de,be,_e,Ne.convert(dt),Ne.convert(_t),Fe)}finally{let it=ne!==null?W.get(ne).__webglFramebuffer:null;z.bindFramebuffer(q.FRAMEBUFFER,it)}}},this.readRenderTargetPixelsAsync=async function(F,ce,de,be,_e,Fe,He,Xe=0){if(!F||!F.isWebGLRenderTarget)throw new Error("THREE.WebGLRenderer.readRenderTargetPixels: renderTarget is not THREE.WebGLRenderTarget.");let Ve=W.get(F).__webglFramebuffer;if(F.isWebGLCubeRenderTarget&&He!==void 0&&(Ve=Ve[He]),Ve){if(ce>=0&&ce<=F.width-be&&de>=0&&de<=F.height-_e){z.bindFramebuffer(q.FRAMEBUFFER,Ve);let it=F.textures[Xe],dt=it.format,_t=it.type;if(F.textures.length>1&&q.readBuffer(q.COLOR_ATTACHMENT0+Xe),!C.textureFormatReadable(dt))throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: renderTarget is not in RGBA or implementation defined format.");if(!C.textureTypeReadable(_t))throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: renderTarget is not in UnsignedByteType or implementation defined type.");let et=q.createBuffer();q.bindBuffer(q.PIXEL_PACK_BUFFER,et),q.bufferData(q.PIXEL_PACK_BUFFER,Fe.byteLength,q.STREAM_READ),q.readPixels(ce,de,be,_e,Ne.convert(dt),Ne.convert(_t),0);let ft=ne!==null?W.get(ne).__webglFramebuffer:null;z.bindFramebuffer(q.FRAMEBUFFER,ft);let Dt=q.fenceSync(q.SYNC_GPU_COMMANDS_COMPLETE,0);return q.flush(),await Kp(q,Dt,4),q.bindBuffer(q.PIXEL_PACK_BUFFER,et),q.getBufferSubData(q.PIXEL_PACK_BUFFER,0,Fe),q.deleteBuffer(et),q.deleteSync(Dt),Fe}throw new Error("THREE.WebGLRenderer.readRenderTargetPixelsAsync: requested read bounds are out of range.")}},this.copyFramebufferToTexture=function(F,ce=null,de=0){let be=Math.pow(2,-de),_e=Math.floor(F.image.width*be),Fe=Math.floor(F.image.height*be),He=ce!==null?ce.x:0,Xe=ce!==null?ce.y:0;Q.setTexture2D(F,0),q.copyTexSubImage2D(q.TEXTURE_2D,de,0,0,He,Xe,_e,Fe),z.unbindTexture()};let Pi=q.createFramebuffer(),yr=q.createFramebuffer();this.copyTextureToTexture=function(F,ce,de=null,be=null,_e=0,Fe=0){let He,Xe,Ve,it,dt,_t,et,ft,Dt,Ut=F.isCompressedTexture?F.mipmaps[Fe]:F.image;if(de!==null)He=de.max.x-de.min.x,Xe=de.max.y-de.min.y,Ve=de.isBox3?de.max.z-de.min.z:1,it=de.min.x,dt=de.min.y,_t=de.isBox3?de.min.z:0;else{let dn=Math.pow(2,-_e);He=Math.floor(Ut.width*dn),Xe=Math.floor(Ut.height*dn),Ve=F.isDataArrayTexture?Ut.depth:F.isData3DTexture?Math.floor(Ut.depth*dn):1,it=0,dt=0,_t=0}be!==null?(et=be.x,ft=be.y,Dt=be.z):(et=0,ft=0,Dt=0);let wt=Ne.convert(ce.format),mt=Ne.convert(ce.type),At;ce.isData3DTexture?(Q.setTexture3D(ce,0),At=q.TEXTURE_3D):ce.isDataArrayTexture||ce.isCompressedArrayTexture?(Q.setTexture2DArray(ce,0),At=q.TEXTURE_2D_ARRAY):(Q.setTexture2D(ce,0),At=q.TEXTURE_2D),q.pixelStorei(q.UNPACK_FLIP_Y_WEBGL,ce.flipY),q.pixelStorei(q.UNPACK_PREMULTIPLY_ALPHA_WEBGL,ce.premultiplyAlpha),q.pixelStorei(q.UNPACK_ALIGNMENT,ce.unpackAlignment);let Ht=q.getParameter(q.UNPACK_ROW_LENGTH),at=q.getParameter(q.UNPACK_IMAGE_HEIGHT),lt=q.getParameter(q.UNPACK_SKIP_PIXELS),xn=q.getParameter(q.UNPACK_SKIP_ROWS),xr=q.getParameter(q.UNPACK_SKIP_IMAGES);q.pixelStorei(q.UNPACK_ROW_LENGTH,Ut.width),q.pixelStorei(q.UNPACK_IMAGE_HEIGHT,Ut.height),q.pixelStorei(q.UNPACK_SKIP_PIXELS,it),q.pixelStorei(q.UNPACK_SKIP_ROWS,dt),q.pixelStorei(q.UNPACK_SKIP_IMAGES,_t);let Zi=F.isDataArrayTexture||F.isData3DTexture,ai=ce.isDataArrayTexture||ce.isData3DTexture;if(F.isDepthTexture){let dn=W.get(F),Un=W.get(ce),zt=W.get(dn.__renderTarget),Nn=W.get(Un.__renderTarget);z.bindFramebuffer(q.READ_FRAMEBUFFER,zt.__webglFramebuffer),z.bindFramebuffer(q.DRAW_FRAMEBUFFER,Nn.__webglFramebuffer);for(let pn=0;pn<Ve;pn++)Zi&&(q.framebufferTextureLayer(q.READ_FRAMEBUFFER,q.COLOR_ATTACHMENT0,W.get(F).__webglTexture,_e,_t+pn),q.framebufferTextureLayer(q.DRAW_FRAMEBUFFER,q.COLOR_ATTACHMENT0,W.get(ce).__webglTexture,Fe,Dt+pn)),q.blitFramebuffer(it,dt,He,Xe,et,ft,He,Xe,q.DEPTH_BUFFER_BIT,q.NEAREST);z.bindFramebuffer(q.READ_FRAMEBUFFER,null),z.bindFramebuffer(q.DRAW_FRAMEBUFFER,null)}else if(_e!==0||F.isRenderTargetTexture||W.has(F)){let dn=W.get(F),Un=W.get(ce);z.bindFramebuffer(q.READ_FRAMEBUFFER,Pi),z.bindFramebuffer(q.DRAW_FRAMEBUFFER,yr);for(let zt=0;zt<Ve;zt++)Zi?q.framebufferTextureLayer(q.READ_FRAMEBUFFER,q.COLOR_ATTACHMENT0,dn.__webglTexture,_e,_t+zt):q.framebufferTexture2D(q.READ_FRAMEBUFFER,q.COLOR_ATTACHMENT0,q.TEXTURE_2D,dn.__webglTexture,_e),ai?q.framebufferTextureLayer(q.DRAW_FRAMEBUFFER,q.COLOR_ATTACHMENT0,Un.__webglTexture,Fe,Dt+zt):q.framebufferTexture2D(q.DRAW_FRAMEBUFFER,q.COLOR_ATTACHMENT0,q.TEXTURE_2D,Un.__webglTexture,Fe),_e!==0?q.blitFramebuffer(it,dt,He,Xe,et,ft,He,Xe,q.COLOR_BUFFER_BIT,q.NEAREST):ai?q.copyTexSubImage3D(At,Fe,et,ft,Dt+zt,it,dt,He,Xe):q.copyTexSubImage2D(At,Fe,et,ft,it,dt,He,Xe);z.bindFramebuffer(q.READ_FRAMEBUFFER,null),z.bindFramebuffer(q.DRAW_FRAMEBUFFER,null)}else ai?F.isDataTexture||F.isData3DTexture?q.texSubImage3D(At,Fe,et,ft,Dt,He,Xe,Ve,wt,mt,Ut.data):ce.isCompressedArrayTexture?q.compressedTexSubImage3D(At,Fe,et,ft,Dt,He,Xe,Ve,wt,Ut.data):q.texSubImage3D(At,Fe,et,ft,Dt,He,Xe,Ve,wt,mt,Ut):F.isDataTexture?q.texSubImage2D(q.TEXTURE_2D,Fe,et,ft,He,Xe,wt,mt,Ut.data):F.isCompressedTexture?q.compressedTexSubImage2D(q.TEXTURE_2D,Fe,et,ft,Ut.width,Ut.height,wt,Ut.data):q.texSubImage2D(q.TEXTURE_2D,Fe,et,ft,He,Xe,wt,mt,Ut);q.pixelStorei(q.UNPACK_ROW_LENGTH,Ht),q.pixelStorei(q.UNPACK_IMAGE_HEIGHT,at),q.pixelStorei(q.UNPACK_SKIP_PIXELS,lt),q.pixelStorei(q.UNPACK_SKIP_ROWS,xn),q.pixelStorei(q.UNPACK_SKIP_IMAGES,xr),Fe===0&&ce.generateMipmaps&&q.generateMipmap(At),z.unbindTexture()},this.initRenderTarget=function(F){W.get(F).__webglFramebuffer===void 0&&Q.setupRenderTarget(F)},this.initTexture=function(F){F.isCubeTexture?Q.setTextureCube(F,0):F.isData3DTexture?Q.setTexture3D(F,0):F.isDataArrayTexture||F.isCompressedArrayTexture?Q.setTexture2DArray(F,0):Q.setTexture2D(F,0),z.unbindTexture()},this.resetState=function(){oe=0,te=0,ne=null,z.reset(),nt.reset()},typeof __THREE_DEVTOOLS__<"u"&&__THREE_DEVTOOLS__.dispatchEvent(new CustomEvent("observe",{detail:this}))}get coordinateSystem(){return Xi}get outputColorSpace(){return this._outputColorSpace}set outputColorSpace(e){this._outputColorSpace=e;let t=this.getContext();t.drawingBufferColorSpace=Mt._getDrawingBufferColorSpace(e),t.unpackColorSpace=Mt._getUnpackColorSpace()}};var Df={type:"change"},td={type:"start"},Nf={type:"end"},xc=new Ti,Uf=new sn,Ag=Math.cos(70*gt.DEG2RAD),en=new N,Ln=2*Math.PI,Rt={NONE:-1,ROTATE:0,DOLLY:1,PAN:2,TOUCH_ROTATE:3,TOUCH_PAN:4,TOUCH_DOLLY_PAN:5,TOUCH_DOLLY_ROTATE:6},ed=1e-6,Mc=class extends qa{constructor(e,t=null){super(e,t),this.state=Rt.NONE,this.target=new N,this.cursor=new N,this.minDistance=0,this.maxDistance=1/0,this.minZoom=0,this.maxZoom=1/0,this.minTargetRadius=0,this.maxTargetRadius=1/0,this.minPolarAngle=0,this.maxPolarAngle=Math.PI,this.minAzimuthAngle=-1/0,this.maxAzimuthAngle=1/0,this.enableDamping=!1,this.dampingFactor=.05,this.enableZoom=!0,this.zoomSpeed=1,this.enableRotate=!0,this.rotateSpeed=1,this.keyRotateSpeed=1,this.enablePan=!0,this.panSpeed=1,this.screenSpacePanning=!0,this.keyPanSpeed=7,this.zoomToCursor=!1,this.autoRotate=!1,this.autoRotateSpeed=2,this.keys={LEFT:"ArrowLeft",UP:"ArrowUp",RIGHT:"ArrowRight",BOTTOM:"ArrowDown"},this.mouseButtons={LEFT:ur.ROTATE,MIDDLE:ur.DOLLY,RIGHT:ur.PAN},this.touches={ONE:dr.ROTATE,TWO:dr.DOLLY_PAN},this.target0=this.target.clone(),this.position0=this.object.position.clone(),this.zoom0=this.object.zoom,this._cursorStyle="auto",this._domElementKeyEvents=null,this._lastPosition=new N,this._lastQuaternion=new Bt,this._lastTargetPosition=new N,this._quat=new Bt().setFromUnitVectors(e.up,new N(0,1,0)),this._quatInverse=this._quat.clone().invert(),this._spherical=new Ds,this._sphericalDelta=new Ds,this._scale=1,this._panOffset=new N,this._rotateStart=new Te,this._rotateEnd=new Te,this._rotateDelta=new Te,this._panStart=new Te,this._panEnd=new Te,this._panDelta=new Te,this._dollyStart=new Te,this._dollyEnd=new Te,this._dollyDelta=new Te,this._dollyDirection=new N,this._mouse=new Te,this._performCursorZoom=!1,this._pointers=[],this._pointerPositions={},this._controlActive=!1,this._onPointerMove=Rg.bind(this),this._onPointerDown=Cg.bind(this),this._onPointerUp=Pg.bind(this),this._onContextMenu=Fg.bind(this),this._onMouseWheel=Dg.bind(this),this._onKeyDown=Ug.bind(this),this._onTouchStart=Ng.bind(this),this._onTouchMove=Og.bind(this),this._onMouseDown=Ig.bind(this),this._onMouseMove=Lg.bind(this),this._interceptControlDown=Bg.bind(this),this._interceptControlUp=zg.bind(this),this.domElement!==null&&this.connect(this.domElement),this.update()}set cursorStyle(e){this._cursorStyle=e,e==="grab"?this.domElement.style.cursor="grab":this.domElement.style.cursor="auto"}get cursorStyle(){return this._cursorStyle}connect(e){super.connect(e),this.domElement.addEventListener("pointerdown",this._onPointerDown),this.domElement.addEventListener("pointercancel",this._onPointerUp),this.domElement.addEventListener("contextmenu",this._onContextMenu),this.domElement.addEventListener("wheel",this._onMouseWheel,{passive:!1}),this.domElement.getRootNode().addEventListener("keydown",this._interceptControlDown,{passive:!0,capture:!0}),this.domElement.style.touchAction="none"}disconnect(){this.domElement.removeEventListener("pointerdown",this._onPointerDown),this.domElement.ownerDocument.removeEventListener("pointermove",this._onPointerMove),this.domElement.ownerDocument.removeEventListener("pointerup",this._onPointerUp),this.domElement.removeEventListener("pointercancel",this._onPointerUp),this.domElement.removeEventListener("wheel",this._onMouseWheel),this.domElement.removeEventListener("contextmenu",this._onContextMenu),this.stopListenToKeyEvents(),this.domElement.getRootNode().removeEventListener("keydown",this._interceptControlDown,{capture:!0}),this.domElement.style.touchAction="auto"}dispose(){this.disconnect()}getPolarAngle(){return this._spherical.phi}getAzimuthalAngle(){return this._spherical.theta}getDistance(){return this.object.position.distanceTo(this.target)}listenToKeyEvents(e){e.addEventListener("keydown",this._onKeyDown),this._domElementKeyEvents=e}stopListenToKeyEvents(){this._domElementKeyEvents!==null&&(this._domElementKeyEvents.removeEventListener("keydown",this._onKeyDown),this._domElementKeyEvents=null)}saveState(){this.target0.copy(this.target),this.position0.copy(this.object.position),this.zoom0=this.object.zoom}reset(){this.target.copy(this.target0),this.object.position.copy(this.position0),this.object.zoom=this.zoom0,this.object.updateProjectionMatrix(),this.dispatchEvent(Df),this.update(),this.state=Rt.NONE}pan(e,t){this._pan(e,t),this.update()}dollyIn(e){this._dollyIn(e),this.update()}dollyOut(e){this._dollyOut(e),this.update()}rotateLeft(e){this._rotateLeft(e),this.update()}rotateUp(e){this._rotateUp(e),this.update()}update(e=null){let t=this.object.position;en.copy(t).sub(this.target),en.applyQuaternion(this._quat),this._spherical.setFromVector3(en),this.autoRotate&&this.state===Rt.NONE&&this._rotateLeft(this._getAutoRotationAngle(e)),this.enableDamping?(this._spherical.theta+=this._sphericalDelta.theta*this.dampingFactor,this._spherical.phi+=this._sphericalDelta.phi*this.dampingFactor):(this._spherical.theta+=this._sphericalDelta.theta,this._spherical.phi+=this._sphericalDelta.phi);let n=this.minAzimuthAngle,i=this.maxAzimuthAngle;isFinite(n)&&isFinite(i)&&(n<-Math.PI?n+=Ln:n>Math.PI&&(n-=Ln),i<-Math.PI?i+=Ln:i>Math.PI&&(i-=Ln),n<=i?this._spherical.theta=Math.max(n,Math.min(i,this._spherical.theta)):this._spherical.theta=this._spherical.theta>(n+i)/2?Math.max(n,this._spherical.theta):Math.min(i,this._spherical.theta)),this._spherical.phi=Math.max(this.minPolarAngle,Math.min(this.maxPolarAngle,this._spherical.phi)),this._spherical.makeSafe(),this.enableDamping===!0?this.target.addScaledVector(this._panOffset,this.dampingFactor):this.target.add(this._panOffset),this.target.sub(this.cursor),this.target.clampLength(this.minTargetRadius,this.maxTargetRadius),this.target.add(this.cursor);let s=!1;if(this.zoomToCursor&&this._performCursorZoom||this.object.isOrthographicCamera)this._spherical.radius=this._clampDistance(this._spherical.radius);else{let a=this._spherical.radius;this._spherical.radius=this._clampDistance(this._spherical.radius*this._scale),s=a!=this._spherical.radius}if(en.setFromSpherical(this._spherical),en.applyQuaternion(this._quatInverse),t.copy(this.target).add(en),this.object.lookAt(this.target),this.enableDamping===!0?(this._sphericalDelta.theta*=1-this.dampingFactor,this._sphericalDelta.phi*=1-this.dampingFactor,this._panOffset.multiplyScalar(1-this.dampingFactor)):(this._sphericalDelta.set(0,0,0),this._panOffset.set(0,0,0)),this.zoomToCursor&&this._performCursorZoom){let a=null;if(this.object.isPerspectiveCamera){let o=en.length();a=this._clampDistance(o*this._scale);let c=o-a;this.object.position.addScaledVector(this._dollyDirection,c),this.object.updateMatrixWorld(),s=!!c}else if(this.object.isOrthographicCamera){let o=new N(this._mouse.x,this._mouse.y,0);o.unproject(this.object);let c=this.object.zoom;this.object.zoom=Math.max(this.minZoom,Math.min(this.maxZoom,this.object.zoom/this._scale)),this.object.updateProjectionMatrix(),s=c!==this.object.zoom;let l=new N(this._mouse.x,this._mouse.y,0);l.unproject(this.object),this.object.position.sub(l).add(o),this.object.updateMatrixWorld(),a=en.length()}else console.warn("WARNING: OrbitControls.js encountered an unknown camera type - zoom to cursor disabled."),this.zoomToCursor=!1;a!==null&&(this.screenSpacePanning?this.target.set(0,0,-1).transformDirection(this.object.matrix).multiplyScalar(a).add(this.object.position):(xc.origin.copy(this.object.position),xc.direction.set(0,0,-1).transformDirection(this.object.matrix),Math.abs(this.object.up.dot(xc.direction))<Ag?this.object.lookAt(this.target):(Uf.setFromNormalAndCoplanarPoint(this.object.up,this.target),xc.intersectPlane(Uf,this.target))))}else if(this.object.isOrthographicCamera){let a=this.object.zoom;this.object.zoom=Math.max(this.minZoom,Math.min(this.maxZoom,this.object.zoom/this._scale)),a!==this.object.zoom&&(this.object.updateProjectionMatrix(),s=!0)}return this._scale=1,this._performCursorZoom=!1,s||this._lastPosition.distanceToSquared(this.object.position)>ed||8*(1-this._lastQuaternion.dot(this.object.quaternion))>ed||this._lastTargetPosition.distanceToSquared(this.target)>ed?(this.dispatchEvent(Df),this._lastPosition.copy(this.object.position),this._lastQuaternion.copy(this.object.quaternion),this._lastTargetPosition.copy(this.target),!0):!1}_getAutoRotationAngle(e){return e!==null?Ln/60*this.autoRotateSpeed*e:Ln/60/60*this.autoRotateSpeed}_getZoomScale(e){let t=Math.abs(e*.01);return Math.pow(.95,this.zoomSpeed*t)}_rotateLeft(e){this._sphericalDelta.theta-=e}_rotateUp(e){this._sphericalDelta.phi-=e}_panLeft(e,t){en.setFromMatrixColumn(t,0),en.multiplyScalar(-e),this._panOffset.add(en)}_panUp(e,t){this.screenSpacePanning===!0?en.setFromMatrixColumn(t,1):(en.setFromMatrixColumn(t,0),en.crossVectors(this.object.up,en)),en.multiplyScalar(e),this._panOffset.add(en)}_pan(e,t){let n=this.domElement;if(this.object.isPerspectiveCamera){let i=this.object.position;en.copy(i).sub(this.target);let s=en.length();s*=Math.tan(this.object.fov/2*Math.PI/180),this._panLeft(2*e*s/n.clientHeight,this.object.matrix),this._panUp(2*t*s/n.clientHeight,this.object.matrix)}else this.object.isOrthographicCamera?(this._panLeft(e*(this.object.right-this.object.left)/this.object.zoom/n.clientWidth,this.object.matrix),this._panUp(t*(this.object.top-this.object.bottom)/this.object.zoom/n.clientHeight,this.object.matrix)):(console.warn("WARNING: OrbitControls.js encountered an unknown camera type - pan disabled."),this.enablePan=!1)}_dollyOut(e){this.object.isPerspectiveCamera||this.object.isOrthographicCamera?this._scale/=e:(console.warn("WARNING: OrbitControls.js encountered an unknown camera type - dolly/zoom disabled."),this.enableZoom=!1)}_dollyIn(e){this.object.isPerspectiveCamera||this.object.isOrthographicCamera?this._scale*=e:(console.warn("WARNING: OrbitControls.js encountered an unknown camera type - dolly/zoom disabled."),this.enableZoom=!1)}_updateZoomParameters(e,t){if(!this.zoomToCursor)return;this._performCursorZoom=!0;let n=this.domElement.getBoundingClientRect(),i=e-n.left,s=t-n.top,a=n.width,o=n.height;this._mouse.x=i/a*2-1,this._mouse.y=-(s/o)*2+1,this._dollyDirection.set(this._mouse.x,this._mouse.y,1).unproject(this.object).sub(this.object.position).normalize()}_clampDistance(e){return Math.max(this.minDistance,Math.min(this.maxDistance,e))}_handleMouseDownRotate(e){this._rotateStart.set(e.clientX,e.clientY)}_handleMouseDownDolly(e){this._updateZoomParameters(e.clientX,e.clientX),this._dollyStart.set(e.clientX,e.clientY)}_handleMouseDownPan(e){this._panStart.set(e.clientX,e.clientY)}_handleMouseMoveRotate(e){this._rotateEnd.set(e.clientX,e.clientY),this._rotateDelta.subVectors(this._rotateEnd,this._rotateStart).multiplyScalar(this.rotateSpeed);let t=this.domElement;this._rotateLeft(Ln*this._rotateDelta.x/t.clientHeight),this._rotateUp(Ln*this._rotateDelta.y/t.clientHeight),this._rotateStart.copy(this._rotateEnd),this.update()}_handleMouseMoveDolly(e){this._dollyEnd.set(e.clientX,e.clientY),this._dollyDelta.subVectors(this._dollyEnd,this._dollyStart),this._dollyDelta.y>0?this._dollyOut(this._getZoomScale(this._dollyDelta.y)):this._dollyDelta.y<0&&this._dollyIn(this._getZoomScale(this._dollyDelta.y)),this._dollyStart.copy(this._dollyEnd),this.update()}_handleMouseMovePan(e){this._panEnd.set(e.clientX,e.clientY),this._panDelta.subVectors(this._panEnd,this._panStart).multiplyScalar(this.panSpeed),this._pan(this._panDelta.x,this._panDelta.y),this._panStart.copy(this._panEnd),this.update()}_handleMouseWheel(e){this._updateZoomParameters(e.clientX,e.clientY),e.deltaY<0?this._dollyIn(this._getZoomScale(e.deltaY)):e.deltaY>0&&this._dollyOut(this._getZoomScale(e.deltaY)),this.update()}_handleKeyDown(e){let t=!1;switch(e.code){case this.keys.UP:e.ctrlKey||e.metaKey||e.shiftKey?this.enableRotate&&this._rotateUp(Ln*this.keyRotateSpeed/this.domElement.clientHeight):this.enablePan&&this._pan(0,this.keyPanSpeed),t=!0;break;case this.keys.BOTTOM:e.ctrlKey||e.metaKey||e.shiftKey?this.enableRotate&&this._rotateUp(-Ln*this.keyRotateSpeed/this.domElement.clientHeight):this.enablePan&&this._pan(0,-this.keyPanSpeed),t=!0;break;case this.keys.LEFT:e.ctrlKey||e.metaKey||e.shiftKey?this.enableRotate&&this._rotateLeft(Ln*this.keyRotateSpeed/this.domElement.clientHeight):this.enablePan&&this._pan(this.keyPanSpeed,0),t=!0;break;case this.keys.RIGHT:e.ctrlKey||e.metaKey||e.shiftKey?this.enableRotate&&this._rotateLeft(-Ln*this.keyRotateSpeed/this.domElement.clientHeight):this.enablePan&&this._pan(-this.keyPanSpeed,0),t=!0;break}t&&(e.preventDefault(),this.update())}_handleTouchStartRotate(e){if(this._pointers.length===1)this._rotateStart.set(e.pageX,e.pageY);else{let t=this._getSecondPointerPosition(e),n=.5*(e.pageX+t.x),i=.5*(e.pageY+t.y);this._rotateStart.set(n,i)}}_handleTouchStartPan(e){if(this._pointers.length===1)this._panStart.set(e.pageX,e.pageY);else{let t=this._getSecondPointerPosition(e),n=.5*(e.pageX+t.x),i=.5*(e.pageY+t.y);this._panStart.set(n,i)}}_handleTouchStartDolly(e){let t=this._getSecondPointerPosition(e),n=e.pageX-t.x,i=e.pageY-t.y,s=Math.sqrt(n*n+i*i);this._dollyStart.set(0,s)}_handleTouchStartDollyPan(e){this.enableZoom&&this._handleTouchStartDolly(e),this.enablePan&&this._handleTouchStartPan(e)}_handleTouchStartDollyRotate(e){this.enableZoom&&this._handleTouchStartDolly(e),this.enableRotate&&this._handleTouchStartRotate(e)}_handleTouchMoveRotate(e){if(this._pointers.length==1)this._rotateEnd.set(e.pageX,e.pageY);else{let n=this._getSecondPointerPosition(e),i=.5*(e.pageX+n.x),s=.5*(e.pageY+n.y);this._rotateEnd.set(i,s)}this._rotateDelta.subVectors(this._rotateEnd,this._rotateStart).multiplyScalar(this.rotateSpeed);let t=this.domElement;this._rotateLeft(Ln*this._rotateDelta.x/t.clientHeight),this._rotateUp(Ln*this._rotateDelta.y/t.clientHeight),this._rotateStart.copy(this._rotateEnd)}_handleTouchMovePan(e){if(this._pointers.length===1)this._panEnd.set(e.pageX,e.pageY);else{let t=this._getSecondPointerPosition(e),n=.5*(e.pageX+t.x),i=.5*(e.pageY+t.y);this._panEnd.set(n,i)}this._panDelta.subVectors(this._panEnd,this._panStart).multiplyScalar(this.panSpeed),this._pan(this._panDelta.x,this._panDelta.y),this._panStart.copy(this._panEnd)}_handleTouchMoveDolly(e){let t=this._getSecondPointerPosition(e),n=e.pageX-t.x,i=e.pageY-t.y,s=Math.sqrt(n*n+i*i);this._dollyEnd.set(0,s),this._dollyDelta.set(0,Math.pow(this._dollyEnd.y/this._dollyStart.y,this.zoomSpeed)),this._dollyOut(this._dollyDelta.y),this._dollyStart.copy(this._dollyEnd);let a=(e.pageX+t.x)*.5,o=(e.pageY+t.y)*.5;this._updateZoomParameters(a,o)}_handleTouchMoveDollyPan(e){this.enableZoom&&this._handleTouchMoveDolly(e),this.enablePan&&this._handleTouchMovePan(e)}_handleTouchMoveDollyRotate(e){this.enableZoom&&this._handleTouchMoveDolly(e),this.enableRotate&&this._handleTouchMoveRotate(e)}_addPointer(e){this._pointers.push(e.pointerId)}_removePointer(e){delete this._pointerPositions[e.pointerId];for(let t=0;t<this._pointers.length;t++)if(this._pointers[t]==e.pointerId){this._pointers.splice(t,1);return}}_isTrackingPointer(e){for(let t=0;t<this._pointers.length;t++)if(this._pointers[t]==e.pointerId)return!0;return!1}_trackPointer(e){let t=this._pointerPositions[e.pointerId];t===void 0&&(t=new Te,this._pointerPositions[e.pointerId]=t),t.set(e.pageX,e.pageY)}_getSecondPointerPosition(e){let t=e.pointerId===this._pointers[0]?this._pointers[1]:this._pointers[0];return this._pointerPositions[t]}_customWheelEvent(e){let t=e.deltaMode,n={clientX:e.clientX,clientY:e.clientY,deltaY:e.deltaY};switch(t){case 1:n.deltaY*=16;break;case 2:n.deltaY*=100;break}return e.ctrlKey&&!this._controlActive&&(n.deltaY*=10),n}};function Cg(r){this.enabled!==!1&&(this._pointers.length===0&&(this.domElement.setPointerCapture(r.pointerId),this.domElement.ownerDocument.addEventListener("pointermove",this._onPointerMove),this.domElement.ownerDocument.addEventListener("pointerup",this._onPointerUp)),!this._isTrackingPointer(r)&&(this._addPointer(r),r.pointerType==="touch"?this._onTouchStart(r):this._onMouseDown(r),this._cursorStyle==="grab"&&(this.domElement.style.cursor="grabbing")))}function Rg(r){this.enabled!==!1&&(r.pointerType==="touch"?this._onTouchMove(r):this._onMouseMove(r))}function Pg(r){switch(this._removePointer(r),this._pointers.length){case 0:this.domElement.releasePointerCapture(r.pointerId),this.domElement.ownerDocument.removeEventListener("pointermove",this._onPointerMove),this.domElement.ownerDocument.removeEventListener("pointerup",this._onPointerUp),this.dispatchEvent(Nf),this.state=Rt.NONE,this._cursorStyle==="grab"&&(this.domElement.style.cursor="grab");break;case 1:let e=this._pointers[0],t=this._pointerPositions[e];this._onTouchStart({pointerId:e,pageX:t.x,pageY:t.y});break}}function Ig(r){let e;switch(r.button){case 0:e=this.mouseButtons.LEFT;break;case 1:e=this.mouseButtons.MIDDLE;break;case 2:e=this.mouseButtons.RIGHT;break;default:e=-1}switch(e){case ur.DOLLY:if(this.enableZoom===!1)return;this._handleMouseDownDolly(r),this.state=Rt.DOLLY;break;case ur.ROTATE:if(r.ctrlKey||r.metaKey||r.shiftKey){if(this.enablePan===!1)return;this._handleMouseDownPan(r),this.state=Rt.PAN}else{if(this.enableRotate===!1)return;this._handleMouseDownRotate(r),this.state=Rt.ROTATE}break;case ur.PAN:if(r.ctrlKey||r.metaKey||r.shiftKey){if(this.enableRotate===!1)return;this._handleMouseDownRotate(r),this.state=Rt.ROTATE}else{if(this.enablePan===!1)return;this._handleMouseDownPan(r),this.state=Rt.PAN}break;default:this.state=Rt.NONE}this.state!==Rt.NONE&&this.dispatchEvent(td)}function Lg(r){switch(this.state){case Rt.ROTATE:if(this.enableRotate===!1)return;this._handleMouseMoveRotate(r);break;case Rt.DOLLY:if(this.enableZoom===!1)return;this._handleMouseMoveDolly(r);break;case Rt.PAN:if(this.enablePan===!1)return;this._handleMouseMovePan(r);break}}function Dg(r){this.enabled===!1||this.enableZoom===!1||this.state!==Rt.NONE||(r.preventDefault(),this.dispatchEvent(td),this._handleMouseWheel(this._customWheelEvent(r)),this.dispatchEvent(Nf))}function Ug(r){this.enabled!==!1&&this._handleKeyDown(r)}function Ng(r){switch(this._trackPointer(r),this._pointers.length){case 1:switch(this.touches.ONE){case dr.ROTATE:if(this.enableRotate===!1)return;this._handleTouchStartRotate(r),this.state=Rt.TOUCH_ROTATE;break;case dr.PAN:if(this.enablePan===!1)return;this._handleTouchStartPan(r),this.state=Rt.TOUCH_PAN;break;default:this.state=Rt.NONE}break;case 2:switch(this.touches.TWO){case dr.DOLLY_PAN:if(this.enableZoom===!1&&this.enablePan===!1)return;this._handleTouchStartDollyPan(r),this.state=Rt.TOUCH_DOLLY_PAN;break;case dr.DOLLY_ROTATE:if(this.enableZoom===!1&&this.enableRotate===!1)return;this._handleTouchStartDollyRotate(r),this.state=Rt.TOUCH_DOLLY_ROTATE;break;default:this.state=Rt.NONE}break;default:this.state=Rt.NONE}this.state!==Rt.NONE&&this.dispatchEvent(td)}function Og(r){switch(this._trackPointer(r),this.state){case Rt.TOUCH_ROTATE:if(this.enableRotate===!1)return;this._handleTouchMoveRotate(r),this.update();break;case Rt.TOUCH_PAN:if(this.enablePan===!1)return;this._handleTouchMovePan(r),this.update();break;case Rt.TOUCH_DOLLY_PAN:if(this.enableZoom===!1&&this.enablePan===!1)return;this._handleTouchMoveDollyPan(r),this.update();break;case Rt.TOUCH_DOLLY_ROTATE:if(this.enableZoom===!1&&this.enableRotate===!1)return;this._handleTouchMoveDollyRotate(r),this.update();break;default:this.state=Rt.NONE}}function Fg(r){this.enabled!==!1&&r.preventDefault()}function Bg(r){r.key==="Control"&&(this._controlActive=!0,this.domElement.getRootNode().addEventListener("keyup",this._interceptControlUp,{passive:!0,capture:!0}))}function zg(r){r.key==="Control"&&(this._controlActive=!1,this.domElement.getRootNode().removeEventListener("keyup",this._interceptControlUp,{passive:!0,capture:!0}))}var fr=gt.clamp,bc=(r,e,t)=>gt.smoothstep(t,r,e),Of=(r,e,t=.35,n=.6)=>bc(0,t,r)*(1-bc(e-n,e,r));function Ff(r,e){let t=new jn(1,1,1),n=[],i=[],s=(L,C,z)=>new ke(L).lerp(new ke(C),z),a=L=>{let C=new hn({color:L,roughness:.86});return n.push(C),C},o=a(e.skin),c=a(s(e.skin,"#d97778",.28)),l=a(e.hair),h=a(s(e.hair,"#e6c49a",.17)),p=a(e.top),f=a(s(e.top,"#263941",.2)),m=a(e.bottom),v=a(s(e.bottom,"#e9dfcf",.14)),g=a("#30323b"),u=a("#302a30"),y=a("#fff9e9");function x(L,C,z,O){let W=new Et(t,O);return W.position.set(...C),W.scale.set(...z),W.castShadow=!0,W.receiveShadow=!0,W.userData.station="resident",L.add(W),i.push(W),W}function S(L,C,z){let O=new Kt;return O.name=C,O.position.set(...z),L.add(O),O}let E=S(r,"hips",[0,.76,0]);x(E,[0,.065,0],[.4,.19,.28],m);let R=S(E,"chest",[0,.12,0]);x(R,[0,.22,0],[.44,.43,.3],p),x(R,[0,.4,-.005],[.39,.1,.28],p),x(R,[0,.025,.002],[.46,.07,.32],f),x(R,[0,.45,.018],[.19,.08,.21],y),x(R,[0,.49,0],[.15,.1,.17],o),x(R,[.115,.255,.157],[.11,.1,.018],f),x(R,[.115,.3,.171],[.12,.022,.025],y);let P=S(R,"head",[0,.58,0]);x(P,[0,.11,0],[.55,.5,.46],o),x(P,[0,.105,.015],[.61,.36,.46],o),x(P,[0,-.15,.015],[.43,.075,.4],o),x(P,[0,.375,-.025],[.62,.1,.51],l),x(P,[-.025,.443,-.04],[.46,.06,.4],l),x(P,[0,.16,-.234],[.59,.4,.1],l);let V=[],X=[],H=[],se=[],oe=[],te=(L,C,z,O=l)=>{let W=S(P,"hair-lock",L);return W.userData.lean=z,V.push(W),x(W,[0,-C[1]/2,0],C,O),W};te([-.215,.345,.227],[.18,.18,.09],-.14),te([-.06,.38,.244],[.15,.12,.075],-.19,h),te([.075,.365,.24],[.12,.105,.07],-.12),te([.225,.34,.18],[.12,.23,.17],.12);for(let L of[-1,1]){x(P,[L*.32,.05,-.002],[.09,.12,.12],o),x(P,[L*.344,.05,.047],[.025,.06,.03],c),te([L*.286,.3,-.032],[.095,.29,.35],L*.035);let C=S(P,"eye",[L*.124,.105,.253]);X.push(C),x(C,[0,0,0],[.11,.12,.018],y);let z=S(C,"pupil",[0,0,.014]);H.push(z),x(z,[0,-.003,0],[.063,.088,.02],u),x(z,[-.014,.025,.013],[.022,.026,.01],y),x(C,[0,.065,.004],[.115,.018,.02],l);let O=S(P,"brow",[L*.125,.227,.256]);se.push(O),x(O,[0,0,0],[.106,.025,.021],l),x(P,[L*.212,-.032,.253],[.077,.035,.014],c);let W=x(P,[L*.045,-.092,.251],[.018,.018,.017],u);oe.push(W)}if(x(P,[0,.013,.263],[.046,.048,.04],o),x(P,[0,-.105,.25],[.078,.018,.018],u),e.hair_style!=="short"){let L=e.hair_style==="long";for(let C of[-1,1])te([C*.285,.12,-.1],[.14,L?.7:.35,.28],C*-.025),te([C*.19,.07,-.262],[.23,L?.73:.31,.13],C*.04)}if(e.accessory==="glasses"){for(let L of[-1,1]){for(let C of[.02,.195])x(P,[L*.133,C,.289],[.226,.022,.025],g);for(let C of[.031,.235])x(P,[L*C,.108,.289],[.022,.178,.025],g);x(P,[L*.285,.158,.115],[.025,.025,.34],g)}x(P,[0,.13,.289],[.065,.022,.025],g)}let ne=[],j=[],J=[],ie=[],re=[],Se=[];for(let L of[-1,1]){let C=S(R,"shoulder",[L*.29,.38,0]);ne.push(C),x(C,[0,-.105,0],[.17,.25,.23],p);let z=S(C,"elbow",[0,-.23,0]);j.push(z),x(z,[0,-.095,0],[.145,.21,.18],p),x(z,[0,-.19,0],[.15,.06,.185],f);let O=S(z,"hand",[0,-.245,.006]);J.push(O),x(O,[0,0,0],[.13,.13,.155],o),x(O,[-L*.064,.015,.029],[.045,.075,.08],o);let W=S(E,"hip",[L*.125,0,0]);ie.push(W),x(W,[0,-.17,0],[.175,.35,.235],m);let Q=S(W,"knee",[0,-.37,0]);re.push(Q),x(Q,[0,-.135,0],[.155,.29,.215],m),x(Q,[0,-.27,0],[.165,.04,.225],v);let xe=S(Q,"ankle",[0,-.33,0]);Se.push(xe),x(xe,[0,.005,.05],[.2,.11,.31],g),x(xe,[0,-.045,.056],[.21,.032,.32],y),x(xe,[0,.064,.11],[.12,.015,.07],y)}let we=0,ze=0,Ae=0,Ie=2.1,ve=-10,Ee=1.3,Le=0,Ye=0,We=0,U=0,D=0,$=6.5,B=-20,w=0,k=37,Y=!1,K=()=>(k=Math.imul(k,1664525)+1013904223>>>0,k/4294967296);function T(L,C,z,O,W){let fe=O-z,Pe=fr(Math.hypot(fe,C),.08,.37+.33-.001),Be=Math.acos(fr((Pe*Pe-.37*.37-.33*.33)/(2*.37*.33),-1,1)),he=Math.atan2(-C,fe)-Math.atan2(.33*Math.sin(Be),.37+.33*Math.cos(Be));ie[L].rotation.x=gt.lerp(he,-Math.PI/2,W),re[L].rotation.x=gt.lerp(Be,Math.PI/2,W),Se[L].rotation.x=-(ie[L].rotation.x+re[L].rotation.x)}function G(L,{distance:C=0,speed:z=0,sitting:O=0,attention:W=0,gazeYaw:Q=0,gazePitch:xe=0,turnRate:fe=0,reduced:Pe=!1}={}){Pe||(we+=L),ze+=C/(e.height||1)*Math.PI*2;let Be=(qe,Oe,Qe)=>Pe?Oe:gt.damp(qe,Oe,Qe,L);Ae=Pe?0:Be(Ae,fr(z/.85,0,1),9),!Pe&&we>=Ie&&(ve=we,Ie=we+2.6+K()*3.6),!Pe&&we>=Ee&&(Le=(K()-.5)*.85,Ye=(K()-.5)*.16,Ee=we+2.4+K()*3.8),!Pe&&we>=$&&(B=we,w=(w+1)%3,$=we+7+K()*5);let he=we-B,me=Pe?0:Of(he,3.6,.8,1)*(1-Ae),M=3.2-W,ge=Pe||W<=0?0:Of(M,3.2,.3,.75),ee=ge*bc(.12,.48,M),De=Pe?0:Math.sin(we*2.15)*.006,Ce=Pe?0:Math.sin(we*.68)*.018*(1-Ae)*(1-O),Ke=O*(1-ge),Je=Ke*me*+(w===1),je=Ke*me*+(w===2),Ne=(1-O)*me*+(w===0)*(1-ge),nt=Pe?0:ge;D=Be(D,fr(Q*.45,-.72,.72)*nt+Math.sin(ze)*.045*Ae,5);let yt=Pe?0:gt.lerp(Le*(1-Ae*.65),fr(Q-D,-1.15,1.15),nt);We=Be(We,yt,5),U=Be(U,Pe?.12*O:(.16+Ye)*Ke-Je*.13+xe*nt+Ye*(1-O)*(1-nt),5),E.position.set(Ce,.76-.048*Ae*(1-O)+Math.cos(ze*2)*.01*Ae*(1-O),.09*O),R.position.y=.12+De,R.rotation.set(.075*Ae+.14*Ke-.08*Ne,D,-Ce*1.3+Math.sin(ze)*.025*Ae-fr(fe,-2,2)*.025*Ae),R.scale.y=1+De*.65,P.rotation.set(U,We,(Pe?0:Math.sin(we*.71)*.025)+.095*ge-.075*Je),X.forEach((qe,Oe)=>{let Qe=we-ve-Oe*.012,wn=Pe||Qe<0||Qe>.19?0:Math.sin(Qe/.19*Math.PI);qe.scale.y=Math.max(.07,1-wn*.97-ge*.12),H[Oe].position.x=Pe?0:fr(yt-We,-.6,.6)*.034+We*.014,H[Oe].position.y=Pe?0:-U*.04,se[Oe].rotation.z=(Oe?-1:1)*(.035+ge*.13+Je*.1),se[Oe].position.y=.227+ge*.018,oe[Oe].position.y=-.09+ge*.013});for(let qe=0;qe<2;qe++){let Oe=qe?1:-1,Qe=((ze/(Math.PI*2)+qe*.5)%1+1)%1,wn=Qe<.6,Dn=(Qe-.6)/.4,Zn=wn?.3-Qe/.6*.6:-.3+bc(0,1,Dn)*.6,gr=wn?0:Math.sin(Dn*Math.PI)*.145;T(qe,Zn*Ae+Oe*.017*(1-Ae),.061+gr*Ae,E.position.y,O),ie[qe].rotation.z=Ce*1.3*(1-O);let Vt=Math.sin(ze+qe*Math.PI+.25)*Ae,Pt=-1.37+((e.height||1)-1)*.9;ne[qe].rotation.set(Vt*.48*(1-O)+Pt*Ke-Ne*1.15,-Oe*.07*Ke,Oe*(.07+.06*Ae+.45*Ne)),j[qe].rotation.set(-.12-Math.max(0,-Vt)*.35-.55*Ke-Ne*.55,0,0),J[qe].rotation.set(-.08*Ke,0,0)}ne[0].rotation.x+=Je*.12,j[0].rotation.x-=Je*.72,ne[1].rotation.y-=je*.3,J[1].rotation.z+=je*.12,ne[1].rotation.x=gt.lerp(ne[1].rotation.x,-.35,ee),ne[1].rotation.z=gt.lerp(ne[1].rotation.z,1.05,ee),j[1].rotation.z=ee*(1.35+Math.sin(M*13)*.2),j[1].rotation.x=gt.lerp(j[1].rotation.x,-.3,ee),J[1].rotation.z+=ee*Math.sin(M*13-.45)*.2,V.forEach((qe,Oe)=>{qe.rotation.z=qe.userData.lean+(Pe?0:Math.sin(we*2.15-Oe*.7)*.012+Math.sin(ze-.6)*Ae*.035),qe.rotation.x=Pe?0:-U*.1+Math.sin(ze-.8)*Ae*.035+fr(fe,-2,2)*(Oe%2?.025:-.025)});let q=!Pe||!Y;return Y=Pe,q}return{meshes:i,pose:G,dispose(){r.clear(),t.dispose(),n.forEach(L=>L.dispose())}}}function Bf(r,e){let t=new Kt;r.add(t);let n=new vn(2.4,2.4),i=new cn({transparent:!0,alphaTest:.12,depthWrite:!0,side:En,toneMapped:!1}),s=new Et(n,i);t.add(s),s.position.y=(e.foot_y-128)*2.4/256,s.userData.station="resident";let a=!1,o=!1,c=!1,l=null,h=0,p="idle",f=-1,m=1,v=new Va().load(e.atlas,u=>{if(a){u.dispose();return}let y=document.createElement("canvas");y.width=u.image.width,y.height=u.image.height;let x=y.getContext("2d",{willReadFrequently:!0});x.drawImage(u.image,0,0),l=x.getImageData(0,0,y.width,y.height),o=!0,i.map=u,i.needsUpdate=!0,f=-1},void 0,()=>{c=!0});v.colorSpace=Xt,v.generateMipmaps=!1,v.minFilter=v.magFilter=nn,v.repeat.set(1/e.columns,1/e.rows),s.visible=!1;let g=s.raycast.bind(s);return s.raycast=(u,y)=>{if(!o||f<0)return;let x=[];g(u,x);for(let S of x){let E=f*256+Math.min(255,Math.floor(S.uv.x*256)),R=e.actions[p].row*256+Math.min(255,Math.floor((1-S.uv.y)*256));l.data[(R*l.width+E)*4+3]>32&&y.push(S)}},{meshes:[s],get ready(){return o},get failed(){return c},pose(u,y){let x=y.sitting>.5?y.attention>.4?"wave_seated":y.reading?"read":"sit":y.attention>.4?"wave":y.speed>.04?"walk":"idle";x!==p&&(p=x,h=0),y.reduced||(h+=u);let S=e.actions[p],E=y.reduced?0:Math.floor(h*S.fps)%S.frames,R=t.rotation.y,P=m;t.rotation.y=y.gazeYaw||0,p==="walk"&&Math.abs(Math.sin(t.rotation.y))>.3&&(m=Math.sin(t.rotation.y)>0?-1:1),p!=="walk"&&(m=1),s.scale.x=m;let V=o&&(f!==E||v.offset.y!==1-(S.row+1)/e.rows);return V&&(f=E,v.offset.set(f/e.columns,1-(S.row+1)/e.rows),s.visible=!0),V||R!==t.rotation.y||P!==m},dispose(){a=!0,t.removeFromParent(),n.dispose(),i.dispose(),v.dispose(),l=null}}}function mr(r,e,t=.25,n=[]){let{minX:i,maxX:s,minZ:a,maxZ:o}=e.bounds;return r.x-t<i||r.x+t>s||r.z-t<a||r.z+t>o?!1:!e.obstacles.some(c=>{if(n.includes(c.id))return!1;let l=r.x-c.x,h=r.z-c.z,p=Math.cos(c.angle)*l-Math.sin(c.angle)*h,f=Math.sin(c.angle)*l+Math.cos(c.angle)*h,m=Math.max(0,Math.abs(p)-c.halfX),v=Math.max(0,Math.abs(f)-c.halfZ);return m*m+v*v<=t*t})}function Ws(r,e,t,n=.25,i=[]){let s=Math.max(1,Math.ceil(Math.hypot(e.x-r.x,e.z-r.z)/.08));for(let a=0;a<=s;a++){let o=a/s;if(!mr({x:r.x+(e.x-r.x)*o,z:r.z+(e.z-r.z)*o},t,n,i))return!1}return!0}function Sc(r,e,t=.25){let n=null,i=1/0;for(let s=e.bounds.minX+t;s<=e.bounds.maxX-t;s+=.28)for(let a=e.bounds.minZ+t;a<=e.bounds.maxZ-t;a+=.28){let o=(s-r.x)**2+(a-r.z)**2;o<i&&mr({x:s,z:a},e,t)&&(n={x:s,z:a},i=o)}return n}function nd(r,e,t,n=.25){if(!mr(r,t,n)||!mr(e,t,n))return[];if(Ws(r,e,t,n))return[{...e}];let i=.28,s=t.bounds.minX+n,a=t.bounds.minZ+n,o=Math.floor((t.bounds.maxX-n-s)/i)+1,c=Math.floor((t.bounds.maxZ-n-a)/i)+1,l=S=>({x:s+S%o*i,z:a+Math.floor(S/o)*i}),h=Array.from({length:o*c},(S,E)=>mr(l(E),t,n));function p(S){return h.map((R,P)=>({ok:R,key:P,distance:Math.hypot(l(P).x-S.x,l(P).z-S.z)})).filter(R=>R.ok).sort((R,P)=>R.distance-P.distance).find(R=>Ws(S,l(R.key),t,n))?.key}let f=p(r),m=p(e);if(f===void 0||m===void 0)return[];let v=[f],g=new Map([[f,null]]);for(let S=0;S<v.length&&!g.has(m);S++){let E=v[S],R=E%o,P=Math.floor(E/o),V=[R>0?E-1:-1,R+1<o?E+1:-1,P>0?E-o:-1,P+1<c?E+o:-1];for(let X of V)X>=0&&h[X]&&!g.has(X)&&Ws(l(E),l(X),t,n)&&(g.set(X,E),v.push(X))}if(!g.has(m))return[];let u=[e];for(let S=m;S!==null;S=g.get(S))u.unshift(l(S));let y=[],x=r;for(let S=0;S<u.length;){let E=S;for(;E+1<u.length&&Ws(x,u[E+1],t,n);)E++;y.push(u[E]),x=u[E],S=E+1}return y}function zf(r,e,t){let n=new Kt;r.add(n),n.visible=!1;let i=null,s="",a="",o=null,c="",l={x:.8,z:2},h=[],p="idle",f="idle",m=4,v=0,g=1,u=.25,y=Math.PI,x=!1,S=0,E=null,R=0,P=0,V=0,X=null,H="";function se(re){let Se=g;i?.dispose(),g=re.height||1,u=.25*g,n.scale.setScalar(g),i=o?.presentation?.mode==="paper"?Bf(n,o.presentation.module):Ff(n,re),g!==Se&&(c="")}function oe(){return{phase:f,goal:p,blocked:x,facing:n.rotation.y,position:{...l},target:h.at(-1)||null,visible:n.visible,label:o?x?E?.desk?"在空处等一会儿，书桌前暂时过不去":"书桌收起了，先在小屋里歇一会儿":f==="seated"?`在书桌旁翻看${o.work?"「"+o.work.title+"」":"手账"}`:f==="walking"?p==="desk"?"走向书桌":"在屋里走走":"在小屋里歇一会儿":"正在读取角色",ref:o?.work?.ref||null,appearance:s?JSON.parse(s):null,presentation:o?.presentation?.mode||"voxel",paperReady:i?.ready??null,paperFailed:i?.failed??!1}}function te(){let re=oe(),Se=JSON.stringify([re.phase,re.goal,re.blocked,re.label,re.visible]);Se!==H&&(H=Se,t?.(re))}function ne(){if(!(!o||!E)){if(x=!1,h=[],!mr(l,E,u)&&(f==="seated"&&E.desk&&c||(l=Sc(l,E,u)),!l)){l={x:.8,z:2},n.visible=!1,x=!0;return}if(p==="desk"&&E.desk){let re=E.desk.approach,Se=E.desk.seat;if(Ws(re,Se,E,u,["desk"])&&(h=nd(l,re,E,u),h.length)){h.push({x:Se.x,z:Se.z,dock:!0}),f="walking";return}x=!0}else p==="desk"&&(x=!0);f="idle",m=12,v=0}}function j(){let re=e(),Se=JSON.stringify(re);if(Se===c)return;let we=f==="seated";E=re,c=Se,we&&(f="idle",l=Sc(l,E,u)||l),ne()}function J(re){let Se=o,we=re?JSON.stringify(re.appearance):"";if(o=re,!re){n.visible=!1,h=[],f="idle",p="idle",c="",P=0,V=0,X=null,v=0,te();return}let ze=re.presentation?.mode==="paper"?re.presentation.id:"voxel";(we!==s||ze!==a)&&(s=we,a=ze,se(re.appearance)),p=re.work?"desk":"idle";let Ae=p!==(Se?.work?"desk":"idle")||JSON.stringify(Se?.work?.ref)!==JSON.stringify(re.work?.ref);j(),Ae&&(f==="seated"&&(l=E.desk?.approach||Sc(l,E,u)||l,f="idle"),ne()),n.visible=!0,te()}function ie(re,{paused:Se=!1,reduced:we=!1,viewer:ze=null}={}){if(!o)return!1;let Ae=n.visible;if(n.visible=!Se,Se)return Ae;let Ie=Math.min(1,Math.max(0,re));R-=Ie,ze&&(X=ze),P=we?0:Math.max(0,P-Ie);let ve=0;if(R<=0&&(j(),R=.5),x&&!mr(l,E,u)&&(n.visible=!1),!n.visible)return te(),Ae;if(f==="idle"&&p!=="desk"&&!we&&(m-=Ie,m<=0)){let k=[{x:.8,z:2.5},{x:1.8,z:-.5},{x:-.6,z:.3},{x:2.6,z:3.6}],Y=k[S++%k.length];h=nd(l,Y,E,u),h.length?f="walking":m=8}if(f==="walking"){if(we)l={...h.at(-1)},h=[];else{let k=0,Y=l;for(let G of h)k+=Math.hypot(G.x-Y.x,G.z-Y.z),Y=G;let K=P>.4?0:.85*Math.min(1,.22+k/.65),T=K*Ie+(V-K)*(1-Math.exp(-6*Ie))/6;for(V=gt.damp(V,K,6,Ie);h.length&&T>0;){let G=h[0],L=Math.hypot(G.x-l.x,G.z-l.z);y=Math.atan2(G.x-l.x,G.z-l.z),L<=T?(l={x:G.x,z:G.z},h.shift(),T-=L,ve+=L):(l.x+=(G.x-l.x)/L*T,l.z+=(G.z-l.z)/L*T,ve+=T,T=0)}}h.length||(f=p==="desk"&&E.desk?"seated":"idle",m=14)}let Ee=f==="seated",Le=v;(f!=="walking"||we)&&(V=0),v=we?Number(Ee):gt.damp(v,Number(Ee),6,Ie),Ee&&(y=E.desk.angle);let Ye=X?Math.atan2(X.x-l.x,X.z-l.z):y,We=P>.4&&!Ee?Ye:y,D=Math.atan2(Math.sin(We-n.rotation.y),Math.cos(We-n.rotation.y))*(we?1:1-Math.exp(-5*Ie));n.rotation.y+=D,n.position.set(l.x,E.desk?(E.desk.seat.y-.76*g)*v:0,l.z);let $=Math.atan2(Math.sin(Ye-n.rotation.y),Math.cos(Ye-n.rotation.y)),B=X?-Math.atan2(X.y-(n.position.y+1.55*g),Math.hypot(X.x-l.x,X.z-l.z)):0,w=i.pose(Ie,{distance:ve,speed:Ie>0?ve/Ie:V,sitting:v,attention:P,gazeYaw:$,gazePitch:gt.clamp(B,-.3,.3),turnRate:Ie>0?D/Ie:0,reduced:we,reading:!!o.work});return te(),Ae!==n.visible||ve>0||w||Le!==v}return{setContext:J,tick:ie,getState:oe,get meshes(){return i?.meshes||[]},greet(re){re&&(X={x:re.x,y:re.y,z:re.z}),P=3.2},dispose(){n.removeFromParent(),i?.dispose()}}}function kf(r){let e=new Kt;e.name="preview-courtyard",r.add(e);let t=new jn(1,1,1),n=-.735,i=(T,G,L=0)=>{let C=Math.sin(T*127.1+G*311.7+L*73.9)*43758.5453;return C-Math.floor(C)},s=(T,G,L=0)=>T>-6.12-L&&T<6.12+L&&G>-4.72-L&&G<5.92+L,a=[[7.25,1.25],[8.6,1.25],[9.15,3.6],[8.6,6.4],[9.8,9.2],[13.2,12.6],[18.5,15.2]];function o(T,G){let L=1/0;for(let C=1;C<a.length;C++){let[z,O]=a[C-1],[W,Q]=a[C],xe=W-z,fe=Q-O,Pe=Math.max(0,Math.min(1,((T-z)*xe+(G-O)*fe)/(xe*xe+fe*fe)));L=Math.min(L,Math.hypot(T-z-xe*Pe,G-O-fe*Pe))}return L}function c(T,G,L=3){let C=document.createElement("canvas");C.width=C.height=T,G(C.getContext("2d"),T);let z=new ri(C);return z.colorSpace=Xt,z.wrapS=z.wrapT=ms,z.repeat.set(L,L),z}let l={meadow:c(512,(T,G)=>{let L=new ke,C=new ke("#637c51"),z=new ke("#93a978");for(let O=0;O<G;O+=4)for(let W=0;W<G;W+=4){let Q=O/G*54,xe=W/G*54,fe=.45+Math.sin(Q*.22+xe*.16)*.2+Math.cos(xe*.29-Q*.12)*.14+i(Q,xe)*.08;L.copy(C).lerp(z,gt.clamp(fe,0,1)),T.fillStyle="#"+L.getHexString(),T.fillRect(O,W,4,4)}},3),forest:c(512,(T,G)=>{let L=new ke,C=new ke("#3b5239"),z=new ke("#677d50");for(let O=0;O<G;O+=4)for(let W=0;W<G;W+=4){let Q=O/G*64,xe=W/G*64,fe=.42+Math.sin(Q*.31+xe*.25)*.22+Math.cos(Q*.18-xe*.27)*.16+i(Q,xe,2)*.14;L.copy(C).lerp(z,gt.clamp(fe,0,1)),T.fillStyle="#"+L.getHexString(),T.fillRect(O,W,4,4)}},3),ocean:c(512,(T,G)=>{let L=new ke,C=new ke("#2b6e84"),z=new ke("#63b2bc"),O=new ke("#cce8ea");for(let W=0;W<G;W+=4)for(let Q=0;Q<G;Q+=4){let xe=W/G*42,fe=Q/G*42,Pe=Math.sin(xe*.45+Math.sin(fe*.25)*1.4)*.5+.5,Be=i(W,Q,3)*.15;L.copy(C).lerp(z,gt.clamp(Pe*.78+Be,0,1)),Pe>.92&&L.lerp(O,(Pe-.92)*5.5),T.fillStyle="#"+L.getHexString(),T.fillRect(W,Q,4,4)}},4),alpine:c(512,(T,G)=>{let L=new ke,C=new ke("#b4c4d0"),z=new ke("#e8eff4");for(let O=0;O<G;O+=4)for(let W=0;W<G;W+=4){let Q=O/G*30,xe=W/G*30,fe=.5+Math.sin(Q*.28+xe*.19)*.28+Math.cos(xe*.31-Q*.14)*.18;L.copy(C).lerp(z,gt.clamp(fe+i(Q,xe,7)*.08,0,1)),T.fillStyle="#"+L.getHexString(),T.fillRect(O,W,4,4)}},2),city:c(256,(T,G)=>{let L=new ke,C=new ke("#3b414a"),z=new ke("#525a66");for(let O=0;O<G;O+=4)for(let W=0;W<G;W+=4){let Q=O%32===0||W%32===0?.18:0;L.copy(C).lerp(z,gt.clamp(.35+i(O,W,5)*.35+Q,0,1)),T.fillStyle="#"+L.getHexString(),T.fillRect(O,W,4,4)}},6),zen:c(512,(T,G)=>{let L=new ke,C=new ke("#cfc9b7"),z=new ke("#e3decb");for(let O=0;O<G;O+=2)for(let W=0;W<G;W+=4){let Q=Math.sin(W/G*Math.PI*36+Math.sin(O/G*Math.PI*4)*1.4)*.5+.5;L.copy(C).lerp(z,gt.clamp(Q*.72+i(O,W,9)*.22,0,1)),T.fillStyle="#"+L.getHexString(),T.fillRect(O,W,2,4)}},4),snow:c(256,(T,G)=>{let L=new ke;for(let C=0;C<G;C+=4)for(let z=0;z<G;z+=4)L.set("#d8e3e8").lerp(new ke("#f6f9f7"),.4+i(C,z)*.6),T.fillStyle="#"+L.getHexString(),T.fillRect(C,z,4,4)},3)},h=new vn(210,210),p=new hn({map:l.meadow,roughness:.96}),f=new Et(h,p);f.rotation.x=-Math.PI/2,f.position.y=n,f.receiveShadow=!0,e.add(f);let m=[t,h],v=[p],g=[],u={};function y(T){let G=new Kt;G.name=`world-${T}`,G.visible=!1,e.add(G);let L=[],C=[],z=[],O=(M,ge,ee,De,Ce,Ke,Je,je=0,Ne=!1)=>L.push({x:M,y:ge,z:ee,w:De,h:Ce,d:Ke,tint:Je,angle:je,snowCap:Ne}),W=(M,ge,ee,De,Ce,Ke,Je="#ffe0a1")=>C.push({x:M,y:ge,z:ee,w:De,h:Ce,d:Ke,tint:Je});function Q(M,ge){for(let ee=-6.3;ee<=6.35;ee+=.5)for(let De of[-4.88,6.06])O(ee,n+.028,De,.48,.095,.44,i(ee,De)>.5?M:ge);for(let ee=-4.4;ee<=5.8;ee+=.5)for(let De of[-6.32,6.32])De>0&&ee>-.3&&ee<2.75||O(De,n+.028,ee,.44,.095,.48,M);O(7.32,-.632,1.25,.64,.19,2.6,ge,0,!0)}function xe(M,ge,ee="#8f7252",De="#6f563d",Ce="#779267",Ke="#8aa376",Je="#ddb2b0",je="#e7c7a0"){O(M,1.42,ge,2.46,.21,.36,ee,0,!0),O(M,1.54,ge,2.38,.04,.29,De,0,!0);for(let Ne of[-.88,.88])O(M+Ne,1.2,ge+.02,.11,.44,.11,De,0,!0);for(let Ne=0;Ne<9;Ne++){let nt=M-1.04+Ne*.26,yt=ge+(i(Ne,71)-.5)*.18,q=.22+i(Ne,72)*.24;O(nt,1.58+q/2,yt,.055,q,.055,Ne%2?Ce:Ke,0,!0),O(nt,1.62+q,yt,.16,.13,.16,Ne%3?Je:je,0,!0),O(nt-.07,1.56+q*.45,yt+.02,.1,.075,.085,Ne%2?Ke:Ce,0,!0)}O(M+.56,1.56,ge+.21,.24,.04,.22,De,0,!0),O(M+.56,1.65,ge+.28,.13,.15,.13,De,0,!0),W(M+.56,1.76,ge+.28,.1,.13,.1,"#ffdfa0"),O(M+.56,1.84,ge+.28,.19,.048,.19,De,0,!0)}function fe(M,ge,ee=1,De="#9d9a84",Ce="#5f4a35",Ke="#ffe0a1"){let Je=n;O(M,Je+.12*ee,ge,.44*ee,.24*ee,.44*ee,De,0,!0),O(M,Je+.44*ee,ge,.17*ee,.44*ee,.17*ee,Ce,0,!0),O(M,Je+.61*ee,ge,.36*ee,.24*ee,.36*ee,De,0,!0),W(M,Je+.67*ee,ge,.22*ee,.21*ee,.22*ee,Ke),O(M,Je+.79*ee,ge,.48*ee,.065*ee,.48*ee,Ce,0,!0),O(M,Je+.86*ee,ge,.24*ee,.1*ee,.24*ee,De,0,!0)}function Pe(M,ge,ee,De,Ce=["#567b53","#6d8c59","#7e9a69","#4e704d","#94a777"],Ke="#8a7860"){O(M,n+1.38*ee,ge,.28*ee,2.76*ee,.28*ee,Ke),O(M-.1*ee,n+.22*ee,ge+.1*ee,.42*ee,.44*ee,.42*ee,Ke),O(M+.32*ee,n+1.94*ee,ge,.7*ee,.18*ee,.18*ee,Ke,-.28);for(let Je=-3;Je<=3;Je++)for(let je=-3;je<=3;je++)for(let Ne=0;Ne<5;Ne++){if(Je*Je+je*je+(Ne-2)*(Ne-2)*1.32>11.4||i(Je+De,je,Ne)<.08)continue;let nt=.37*ee;O(M+Je*nt,n+(2.15+Ne*.33)*ee,ge+je*nt,nt*1.03,.36*ee,nt*1.03,Ce[Math.floor(i(Je,je+De,Ne)*Ce.length)],0,!0)}}function Be(M,ge,ee,De,Ce=["#2d4e3e","#386049","#477255","#244033","#568160"],Ke="#5d4736"){O(M,n+1.5*ee,ge,.26*ee,3*ee,.26*ee,Ke);let Je=6;for(let je=0;je<Je;je++){let Ne=Math.max(.35,2.15-je*.32)*ee,nt=n+(1.45+je*.66)*ee,yt=.42*ee;for(let q=-Ne;q<=Ne;q+=yt)for(let qe=-Ne;qe<=Ne;qe+=yt){if(Math.hypot(q,qe)>Ne*(.86+i(q+De,qe,je)*.24))continue;let Qe=Ce[Math.floor(i(q*3+De,qe*3,je)*Ce.length)];O(M+q,nt,ge+qe,yt*1.04,.48*ee,yt*1.04,Qe,0,!0)}}O(M,n+(1.45+Je*.66)*ee,ge,.32*ee,.56*ee,.32*ee,Ce[2],0,!0)}function he(M,ge,ee=1,De=0,Ce=["#e7c7a0","#ddb2b0","#d4ad76"]){for(let Ke=0;Ke<3;Ke++){let Je=M+(i(Ke,De,2)-.5)*.32*ee,je=ge+(i(Ke,3,De)-.5)*.26*ee,Ne=(.1+i(Ke,De,4)*.14)*ee;O(Je,n+Ne/2,je,.044*ee,Ne,.044*ee,Ke%2?"#739064":"#88a474",0,!0),Ke!==2&&O(Je,n+Ne+.032*ee,je,.088*ee,.075*ee,.088*ee,Ce[(Ke+Math.floor(De))%Ce.length],0,!0)}}function me(M){let ge=new hn({roughness:M.roughness??.96,metalness:M.metalness??.02});v.push(ge);let ee=new Tn(t,ge,Math.max(1,L.length)),De=new st,Ce=new N,Ke=new Bt,Je=new N,je=new ke;L.forEach((Oe,Qe)=>{Ce.set(Oe.x,Oe.y,Oe.z),Je.set(Oe.w,Oe.h,Oe.d),Ke.setFromAxisAngle(Gt.DEFAULT_UP,Oe.angle),ee.setMatrixAt(Qe,De.compose(Ce,Ke,Je)),ee.setColorAt(Qe,je.set(Oe.tint))}),ee.count=L.length,ee.castShadow=!0,ee.receiveShadow=!0,ee.computeBoundingSphere(),G.add(ee),g.push(ee);let Ne=null,nt=null;C.length&&(nt=new cn({color:"#ffffff",toneMapped:!1}),v.push(nt),Ne=new Tn(t,nt,C.length),C.forEach((Oe,Qe)=>{Ce.set(Oe.x,Oe.y,Oe.z),Je.set(Oe.w,Oe.h,Oe.d),Ke.identity(),Ne.setMatrixAt(Qe,De.compose(Ce,Ke,Je)),Ne.setColorAt(Qe,je.set(Oe.tint))}),Ne.computeBoundingSphere(),G.add(Ne),g.push(Ne));let yt=new Map;L.filter(Oe=>Oe.snowCap).forEach(Oe=>{let Qe=`${Oe.x.toFixed(1)}:${Oe.z.toFixed(1)}`;(!yt.has(Qe)||yt.get(Qe).y<Oe.y)&&yt.set(Qe,Oe)});let q=new hn({color:"#edf4f6",roughness:.96});v.push(q);let qe=new Tn(t,q,Math.max(1,yt.size));[...yt.values()].forEach((Oe,Qe)=>{Ce.set(Oe.x,Oe.y+Oe.h/2+.026,Oe.z),Je.set(Oe.w*1.01,.064,Oe.d*1.01),Ke.identity(),qe.setMatrixAt(Qe,De.compose(Ce,Ke,Je))}),qe.count=yt.size,qe.receiveShadow=!0,qe.computeBoundingSphere(),qe.visible=!1,G.add(qe),g.push(qe),u[T]={group:G,mesh:ee,material:ge,glowMesh:Ne,glowMat:nt,snowCaps:qe,animated:z,textureKey:M.textureKey,roughness:M.roughness??.96,lawnTint:M.lawnTint||"#ffffff",ambientParticleColor:M.ambientParticleColor||"#ffe4a2",ambientAlwaysOn:!!M.ambientAlwaysOn}}return{group:G,box:O,glowBox:W,animated:z,foundationBorder:Q,windowBox:xe,stoneLantern:fe,broadleafTree:Pe,pineTree:Be,flowerCluster:he,finalize:me}}{let T=y("classic");T.foundationBorder("#c4c2a8","#b5b69f");for(let C=7.2;C<19.5;C+=.46)for(let z=-.2;z<17;z+=.46){if(o(C,z)>1.02||s(C,z,.1))continue;let O=["#cbc6ac","#d6d1ba","#c3c0a7","#dfd8bf"];T.box(C,n+.024,z,.44,.07,.44,O[Math.floor(i(C,z,1)*O.length)],0,i(C,z)>.45)}for(let C of[-1.25,1.25])T.box(9.2+C,n+1.35,3.3,.18,2.7,.18,"#7e5e40",0,!0),T.box(9.2+C,n+1.35,4.4,.18,2.7,.18,"#7e5e40",0,!0),T.box(9.2+C,n+2.72,3.85,.18,.18,1.5,"#8f6c49",0,!0);for(let C=0;C<5;C++)T.box(9.2,n+2.86,3.15+C*.35,3.1,.12,.14,"#9d7954",0,!0);for(let C=0;C<16;C++){let z=C/15;T.box(7.9+z*2.6,n+2.96+C%2*.07,3.25+C%3*.45,.26,.16,.24,C%3?"#6c8c5c":"#89a570",0,!0),C%3===1&&T.box(7.95+z*2.5,n+3.04,3.45+C%2*.5,.14,.12,.14,"#e7b9b5",0,!0)}T.box(9.2,n+2.54,3.85,.05,.32,.05,"#4b3b2d"),T.glowBox(9.2,n+2.34,3.85,.18,.22,.18,"#ffe2a2"),T.box(9.2,n+2.46,3.85,.24,.05,.24,"#5d4834");for(let C=0;C<180;C++){let z=i(C,1)*34-17,O=i(C,2)*32-15;if(s(z,O,.65)||o(z,O)<1.2)continue;let W=.65+i(C,3)*.7;for(let Q=0;Q<3;Q++){let xe=z+(Q-1)*.095*W,fe=(.17+i(z,O,Q)*.15)*W;T.box(xe,n+fe/2,O+Q%2*.08,.055*W,fe,.05*W,i(z,O)>.5?"#6f8c66":"#8ca276",(Q-1)*.25)}C%5===0&&T.flowerCluster(z,O,.72,C)}for(let C=0;C<36;C++){let z=-5.9+i(C,30)*11.8,O=C%2?-5.22-i(C,31)*.22:6.38+i(C,32)*.22;o(z,O)<1.1||T.flowerCluster(z,O,.7+i(C,33)*.25,C)}for(let[C,z,O,W]of[[-8.8,-4.7,1.22,1],[-5.3,-9.2,1.28,2],[-7.1,-8.7,.98,11],[4.1,-9.4,1.38,3],[9.4,-5.4,1.1,4],[-13.5,2.5,1.18,5],[-15,-13,1.85,6],[0,-18,1.85,7],[12.8,-15.5,1.75,8],[-20,-2,1.55,9],[21,-7,1.82,10]])T.broadleafTree(C,z,O,W);let G=-7.65,L=6.85;T.box(G,n+.025,L,2.3,.055,1.42,"#bcb9a0",-.18,!0);for(let C=0;C<3;C++)T.box(G+(C-1)*.33,n+.5,L,.3,.085,1.12,C%2?"#a9855e":"#b89469",-.18,!0);for(let C of[-.93,.93])T.box(G+C,n+.25,L-.39,.12,.5,.12,"#72583e",-.18,!0),T.box(G+C,n+.25,L+.39,.12,.5,.12,"#72583e",-.18,!0),T.box(G+C,n+.8,L-.42,.1,.62,.1,"#72583e",-.18,!0);for(let C=0;C<4;C++)T.box(G,n+.84+C*.14,L-.42,1.94,.1,.1,C%2?"#a9855e":"#b89469",-.18,!0);T.stoneLantern(-10.55,4.05,1),T.stoneLantern(8.55,2.1,.92),T.stoneLantern(11.95,11.45,.88),T.stoneLantern(-4.55,-5.35,.82),T.windowBox(-3.6,-4.78);for(let C of[-1,1]){let z=C<0?-5.1:-2.05;for(let O=0;O<8;O++){let W=1.75+O*.3,Q=-4.65+(i(O,C+80)-.5)*.05;T.box(z+C*.03,W,Q,.15,.11,.07,O%2?"#6f8b60":"#7e9a69",0,!0),O%3===1&&T.box(z+C*.02,W+.06,Q+.02,.11,.08,.06,"#d9b47e",0,!0)}}for(let C=0;C<13;C++){let z=-10.5+C*1.48;if(T.box(z,n+.54,-7.1,.11,1.08,.11,"#c2bfa6"),C<12)for(let O of[.36,.77])T.box(z+.74,n+O,-7.1,1.47,.095,.085,"#d2ceb5",0,O>.5)}T.finalize({textureKey:"meadow",roughness:.96,ambientParticleColor:"#ffe4a2"})}{let T=y("cabin");T.foundationBorder("#73795e","#5b624b");let G=[[-16,-9.5],[-11,-8.2],[-6.5,-8.8],[3,-8.5],[8.8,-5.8],[11.6,-1.2],[11.9,4.5],[9.8,9.6],[6.2,13.5],[2.5,17.5]],L=(C,z)=>{let O=1/0;for(let W=1;W<G.length;W++){let[Q,xe]=G[W-1],[fe,Pe]=G[W],Be=fe-Q,he=Pe-xe,me=Math.max(0,Math.min(1,((C-Q)*Be+(z-xe)*he)/(Be*Be+he*he)));O=Math.min(O,Math.hypot(C-Q-Be*me,z-xe-he*me))}return O};for(let C=-15;C<=15;C+=.55)for(let z=-11;z<=16;z+=.55){if(s(C,z,.8))continue;let O=L(C,z);if(O<1.15){let W=O<.65?i(C,z,5)>.5?"#4a848c":"#56959c":"#6ba8ad";T.box(C,n+.012,z,.54,.04,.54,W)}else if(O<1.65&&i(C,z,8)>.35){let W=.09+i(C,z,9)*.14;T.box(C,n+W/2,z,.42,W,.42,i(C,z,4)>.5?"#7a8072":"#67725c",0,!0)}}for(let C=0;C<11;C++){let z=7.4+C*.55,O=Math.sin(C/10*Math.PI)*.46;if(T.box(z,n+.16+O,1.25,.46,.1,2.2,C%2?"#7a583b":"#896444",0,!0),C%2===0)for(let W of[-1.02,1.02])T.box(z,n+.62+O,1.25+W,.12,.86,.12,"#5c422c",0,!0)}for(let C of[-1.02,1.02])T.box(10.15,n+1.15,1.25+C,5.6,.1,.12,"#6c4e35",0,!0);for(let[C,z,O,W]of[[-9.5,-5.2,1.35,1],[-6.2,-9.6,1.5,2],[-3.2,-10.8,1.3,3],[.8,-9.8,1.45,4],[5.2,-9.2,1.55,5],[9.8,-8.5,1.4,6],[-12.5,-1.8,1.45,7],[-11.2,3.8,1.25,8],[-14.8,8.2,1.35,9],[14.8,-3.5,1.45,10],[16.2,2.8,1.55,11],[14.5,8.5,1.35,12],[-16.5,-11.5,2,13],[-8.5,-16.5,2.1,14],[2.5,-17.5,2.2,15],[13.5,-14.5,2,16],[-19.5,1.5,1.85,17],[19.8,-7.2,1.9,18]])T.pineTree(C,z,O,W);T.broadleafTree(-7.6,-7.8,1.05,21,["#658852","#88a45c","#c8a45a","#527345","#a99953"],"#d7d0be"),T.broadleafTree(7.8,-6.2,.95,22,["#587d4b","#779759","#bd9852","#47693f"],"#d7d0be");for(let[C,z,O]of[[-8.2,1.8,1.2],[-8.6,5.8,1.4],[-3.8,7.6,1.1],[3.5,7.9,1.25],[8.5,5.4,1.15],[-2,-6.6,1.3]])T.box(C,n+.32*O,z,.95*O,.64*O,.82*O,"#6b7068",i(C,z)*.5,!0),T.box(C+.1*O,n+.66*O,z-.05*O,.72*O,.12*O,.64*O,"#5a7c4b",0,!0);for(let[C,z]of[[-7.2,3.2],[-6.8,3.6],[-4.4,6.8],[2.4,7.1],[7.8,4.4],[-7.4,-3.2]])T.box(C,n+.12,z,.08,.24,.08,"#e6dec8"),T.box(C,n+.27,z,.24,.12,.24,"#b85549",0,!0),T.box(C+.06,n+.34,z+.05,.05,.03,.05,"#f5ecd9");T.stoneLantern(-7.4,6.2,.95,"#7d8274","#4f4030","#ffd98a"),T.stoneLantern(7.7,-.4,.9,"#7d8274","#4f4030","#ffd98a"),T.stoneLantern(12.6,2.7,.9,"#7d8274","#4f4030","#ffd98a"),T.stoneLantern(-4.4,-5.6,.85,"#7d8274","#4f4030","#ffd98a"),T.windowBox(-3.6,-4.78,"#6f5036","#4c3624","#4c7548","#6f925b","#e8c488","#d78b72");for(let C=0;C<160;C++){let z=i(C,41)*30-15,O=i(C,42)*28-13;if(s(z,O,.7)||L(z,O)<1.3)continue;let W=.2+i(C,43)*.28;T.box(z,n+W/2,O,.22,W,.22,C%3?"#4b6f47":"#648653",i(C,44),!0),C%9===0&&T.flowerCluster(z,O,.65,C,["#f0dca8","#e5c19c","#d8e5b8"])}T.finalize({textureKey:"forest",roughness:.98,ambientParticleColor:"#c8f09c",ambientAlwaysOn:!0})}{let C=function(he,me,M,ge,ee="#e7e2d5",De="#ffffff"){let Ce=new Kt;Ce.position.set(he,n+.12,me);let Ke=new hn({roughness:.7});v.push(Ke);let Je=[[0,.12,0,1.8,.32,.76,ee],[0,.32,0,1.45,.12,.6,"#4c8398"],[.05,1.25,0,.08,1.9,.08,"#8b6d4c"],[.45,1.3,0,.75,1.25,.06,De],[-.32,1.1,0,.55,.9,.06,"#f2ebd9"]],je=new Tn(t,Ke,Je.length),Ne=new st,nt=new N,yt=new Bt,q=new N,qe=new ke;Je.forEach((Oe,Qe)=>{nt.set(Oe[0]*M,Oe[1]*M,Oe[2]*M),q.set(Oe[3]*M,Oe[4]*M,Oe[5]*M),je.setMatrixAt(Qe,Ne.compose(nt,yt,q)),je.setColorAt(Qe,qe.set(Oe[6]))}),je.castShadow=!0,Ce.add(je),T.group.add(Ce),g.push(je),T.animated.push({update(Oe){Ce.position.y=n+.14+Math.sin(Oe*1.4+ge)*.1,Ce.rotation.z=Math.sin(Oe*1.1+ge)*.06,Ce.rotation.y=Math.sin(Oe*.25+ge)*.15+.4}})},T=y("coastal");for(let he=-8.6;he<=9.6;he+=.95)for(let me=-7.4;me<=8.6;me+=.95){let M=(he-.4)/9.2,ge=(me-.5)/8.2,ee=Math.hypot(M,ge)+(i(he,me,15)-.5)*.16;if(ee>1.05)continue;let De=ee>.82,Ce=De?.22:.42;T.box(he,n+Ce/2,me,.96,Ce,.96,De?i(he,me)>.5?"#e5d9bc":"#d9cca9":i(he,me)>.5?"#e6e3d3":"#d6d4c3",0,!0)}T.foundationBorder("#dcdad0","#c9cfc7");for(let he=0;he<14;he++){let me=7.1+he*.62;if(T.box(me,n+.44,1.25,.52,.1,2.35,he%2?"#cbb28c":"#bfa37a",0,!0),he%3===0)for(let M of[-1.05,1.05])T.box(me,n+.46,1.25+M,.16,1.12,.16,"#856a4c",0,!0),T.box(me,n+1.04,1.25+M,.2,.08,.2,"#f4f2e8",0,!0)}for(let he of[-1.05,1.05])T.box(11.1,n+.86,1.25+he,8.2,.05,.05,"#dfd3b8");for(let he=-7.4;he<=7.8;he+=1.45)T.box(he,n+.78,-6.2,.24,.76,.24,"#f4f3ea",0,!0),he<7&&T.box(he+.72,n+1.12,-6.2,1.46,.12,.22,"#4e869c",0,!0);let G=-11.5,L=-13.5;for(let he=-2;he<=2;he++)for(let me=-2;me<=2;me++){if(Math.hypot(he,me)>2.4)continue;let M=.7+(2.5-Math.hypot(he,me))*.55;T.box(G+he*.95,n+M/2,L+me*.95,.98,M,.98,(he+me)%2?"#7f8689":"#6c757a",0,!0)}for(let he=0;he<9;he++){let me=1.85-he*.1;T.box(G,n+1.7+he*.78,L,me,.78,me,he%2?"#c95b52":"#f4f1e6",0,he===8)}T.box(G,n+8.85,L,1.55,.18,1.55,"#3e5f70",0,!0),T.glowBox(G,n+9.45,L,.95,.95,.95,"#fff0b0"),T.box(G,n+10.1,L,1.35,.35,1.35,"#c95b52",0,!0),T.box(G,n+10.5,L,.65,.45,.65,"#c95b52",0,!0);for(let[he,me,M]of[[11.5,-11.8,1.4],[-14.2,-3.5,1.1],[15.5,9.8,1.2],[-10.5,10.5,1]])T.box(he,n+.65*M,me,2.2*M,1.3*M,1.8*M,"#818a8e",.3,!0),T.box(he+.4*M,n+1.2*M,me-.3*M,1.2*M,.9*M,1.2*M,"#949ca0",.2,!0);for(let[he,me,M,ge]of[[-7.2,-4.6,.95,31],[-7.5,4.8,.9,32],[6.6,-5.2,1,33]])T.broadleafTree(he,me,M,ge,["#58856d","#6e9a81","#85ab92","#476e5c"],"#9c8d74");for(let he=0;he<22;he++){let me=he/22*Math.PI*2,M=Math.cos(me)*7.2,ge=Math.sin(me)*6.5;s(M,ge,.5)||M>5.5&&Math.abs(ge-1.25)<2.2||T.flowerCluster(M,ge,.85,he+70,["#e37c8b","#f4ede0","#7fb5c7"])}T.box(-7.1,n+.55,6.2,1.9,.12,.9,"#f3eee2",-.22,!0),T.box(-7.8,n+.78,6.05,.5,.42,.88,"#568fa4",-.22,!0),T.box(-6.6,n+1.45,7.1,.1,2.3,.1,"#c7ab82");for(let he=-3;he<=3;he++)for(let me=-3;me<=3;me++){if(Math.hypot(he,me)>3.3)continue;let M=Math.hypot(he,me)*.08;T.box(-6.6+he*.34,n+2.65-M,7.1+me*.34,.34,.1,.34,(Math.abs(he)+Math.abs(me))%2?"#f6f2e8":"#4b8ea4",0,!0)}T.stoneLantern(7.8,-.35,.9,"#ebe8dc","#4a7f94","#ffe7a8"),T.stoneLantern(14.5,2.4,.85,"#ebe8dc","#4a7f94","#ffe7a8"),T.windowBox(-3.6,-4.78,"#5e8d9b","#3d6f84","#5c8c72","#7aa68d","#e37c8b","#f6f2e8"),C(-3.5,-13.2,1.15,.4),C(16.8,-5.2,1,2.2,"#c86c5a","#faf5ea"),C(9.5,13.8,.92,4.1);let z=new cn({color:"#eaf7f8",transparent:!0,opacity:.46,depthWrite:!1});v.push(z);let O=44,W=new Tn(t,z,O),Q=Array.from({length:O},(he,me)=>{let M=me/O*Math.PI*2,ge=10.2+i(me,91)*14;return{x:Math.cos(M)*ge,z:Math.sin(M)*ge,w:1.1+i(me,92)*1.8,phase:i(me,93)*6.28}});T.group.add(W),g.push(W);let xe=new st,fe=new N,Pe=new Bt,Be=new N;T.animated.push({update(he){Q.forEach((me,M)=>{let ge=Math.sin(he*.9+me.phase)*.55;fe.set(me.x+ge,n+.03,me.z+Math.cos(he*.6+me.phase)*.25),Be.set(me.w*(.75+.25*Math.sin(he*1.3+me.phase)),.03,.16),W.setMatrixAt(M,xe.compose(fe,Pe,Be))}),W.instanceMatrix.needsUpdate=!0}}),T.finalize({textureKey:"ocean",roughness:.72,ambientParticleColor:"#e8fbff",ambientAlwaysOn:!0})}{let G=function(fe,Pe,Be,he,me){let ge=Math.floor(he/1.15);for(let ee=0;ee<ge;ee++){let De=1-ee/ge,Ce=Be*De,Ke=n+.55+ee*1.12,Je=ee>ge*.58;for(let je=-Ce;je<=Ce;je+=1.65)for(let Ne=-Ce;Ne<=Ce;Ne+=1.65){if(Math.hypot(je,Ne)>Ce*(.85+i(je+me,Ne,ee)*.28))continue;let yt=Je?i(je,Ne,ee)>.35?"#edf3f7":"#dbe6ee":ee>ge*.32?i(je,Ne,ee)>.5?"#7b8796":"#6a7788":i(je,Ne,ee)>.5?"#5b7167":"#4d635b";T.box(fe+je,Ke,Pe+Ne,1.65*1.04,1.18,1.65*1.04,yt,0,Je)}}},T=y("french");for(let fe=-8.2;fe<=9.2;fe+=.92)for(let Pe=-7;Pe<=8.2;Pe+=.92){let Be=Math.hypot((fe-.4)/8.8,(Pe-.5)/7.8)+(i(fe,Pe,25)-.5)*.14;if(Be>1.03)continue;let he=Be>.84?.28:.48,me=Be>.88?"#8e96a1":i(fe,Pe)>.5?"#d8d1c2":"#cbc4b4";T.box(fe,n+he/2,Pe,.94,he,.94,me,0,!0)}T.foundationBorder("#d9d2c3","#c8c0b0");for(let fe=-7.2;fe<=7.4;fe+=1.2)T.box(fe,n+.82,-5.9,.22,.84,.22,"#eee7d8",0,!0),fe<7&&T.box(fe+.6,n+1.24,-5.9,1.22,.12,.26,"#f4ede0",0,!0);for(let fe=-5.4;fe<=6.2;fe+=1.25)T.box(-7.2,n+.82,fe,.22,.84,.22,"#eee7d8",0,!0),fe<5.6&&T.box(-7.2,n+1.24,fe+.62,.26,.12,1.26,"#f4ede0",0,!0);for(let fe=0;fe<6;fe++)T.box(7.2+fe*.55,n+.46,1.25,.5,.14,2.5,fe%2?"#ded7c7":"#d0c8b8",0,!0);T.box(9.6,n+.92,.45,.12,.95,.12,"#5c4b3c"),T.box(9.6,n+1.46,.45,.18,.18,.95,"#cfa75f",-.45),T.box(9.6,n+1.48,.9,.24,.24,.2,"#a98142",-.45),G(-16.5,-18.5,8.2,13.5,1),G(-3.2,-23.5,9.8,16.5,2),G(12.8,-19.5,8.8,14.5,3),G(-22.5,-4.5,7.2,10.8,4),G(22.5,-7.2,7.6,11.4,5);for(let[fe,Pe,Be,he]of[[-7.2,-4.8,1.05,51],[-7.5,4.6,.95,52],[7.2,-5.2,1.1,53],[-5.2,-8.2,.85,54]])T.pineTree(fe,Pe,Be,he,["#3a5a52","#4c6d63","#608174","#2f4a44"],"#675748");T.stoneLantern(-6.8,6.2,.95,"#e6dfd0","#9c876a","#ffe6b0"),T.stoneLantern(8.6,2.4,.9,"#e6dfd0","#9c876a","#ffe6b0"),T.stoneLantern(-4.2,-5.6,.85,"#e6dfd0","#9c876a","#ffe6b0"),T.windowBox(-3.6,-4.78,"#d9c7a9","#b59b77","#678572","#84a08b","#f3e8d8","#d7a6ab");let L=new hn({color:"#f5f8fb",roughness:1,transparent:!0,opacity:.78,depthWrite:!1});v.push(L);let C=[];for(let fe=0;fe<42;fe++){let Pe=fe/42*Math.PI*2,Be=10.8+i(fe,101)*14;C.push({x:Math.cos(Pe)*Be,y:n+.2+(i(fe,102)-.3)*2.2,z:Math.sin(Pe)*Be,sx:3.4+i(fe,103)*3.8,sy:.7+i(fe,104)*.8,sz:2.2+i(fe,105)*2.6,speed:.2+i(fe,106)*.35,phase:i(fe,107)*6.28})}let z=new Tn(t,L,C.length);T.group.add(z),g.push(z);let O=new st,W=new N,Q=new Bt,xe=new N;T.animated.push({update(fe,Pe){L.color.set(Pe==="night"?"#5a7290":Pe==="dusk"?"#f4c8b6":Pe==="dawn"?"#f5e3d6":"#f5f8fb"),C.forEach((Be,he)=>{W.set(Be.x+Math.sin(fe*Be.speed+Be.phase)*1.4,Be.y+Math.cos(fe*.3+Be.phase)*.18,Be.z),xe.set(Be.sx,Be.sy,Be.sz),z.setMatrixAt(he,O.compose(W,Q,xe))}),z.instanceMatrix.needsUpdate=!0}}),T.finalize({textureKey:"alpine",roughness:.92,ambientParticleColor:"#fff2d2",ambientAlwaysOn:!0})}{let T=y("loft");for(let O=-8.4;O<=9.4;O+=1.2)for(let W=-6.8;W<=8.2;W+=1.2)T.box(O,n+.18,W,1.18,.36,1.18,Math.round(O+W)%2?"#6d747c":"#626972");T.foundationBorder("#596068","#4d545c");for(let O=-8.4;O<=9.4;O+=1.2)T.box(O,n+.64,-6.9,1.18,.56,.34,"#7a7068",0,!0),T.box(O,n+.96,-6.9,1.22,.1,.42,"#979ba0",0,!0);for(let O=-6.4;O<=8;O+=1.2)T.box(-8.5,n+.64,O,.34,.56,1.18,"#7a7068",0,!0),T.box(-8.5,n+.96,O,.42,.1,1.22,"#979ba0",0,!0);[[-7.6,7.2],[7.8,7.2],[8.2,-5.8],[-7.6,-5.8]].forEach(([O,W])=>{T.box(O,n+1.9,W,.1,3.2,.1,"#333b42"),T.glowBox(O,n+3.45,W,.16,.16,.16,"#ffd28a")});for(let O=1;O<8;O++){let W=O/8,Q=Math.sin(W*Math.PI)*.42;T.glowBox(-7.6+W*15.4,n+3.4-Q,7.2,.12,.12,.12,O%2?"#ffcf82":"#ffe6b3"),T.glowBox(8.2,n+3.4-Q,-5.8+W*13,.12,.12,.12,O%2?"#ffe6b3":"#ffcf82")}let L=-9.6,C=-8.6;for(let O of[-.65,.65])for(let W of[-.65,.65])T.box(L+O,n+1.4,C+W,.16,2.8,.16,"#394248");T.box(L,n+2.85,C,2.1,.16,2.1,"#444d54"),T.box(L,n+4.05,C,1.85,2.2,1.85,"#8a684e");for(let O of[3.4,4.05,4.7])T.box(L,n+O,C,1.92,.09,1.92,"#394248");T.box(L,n+5.3,C,2.1,.25,2.1,"#4b555c",0,!0),T.box(L,n+5.6,C,1.3,.35,1.3,"#4b555c",0,!0),[[-15.5,-15.2,4,12.8,3.8,"#47515e","#ffcf87"],[-10.2,-17.5,4.6,16.5,4.2,"#3b4654","#8ce4f2"],[-4.2,-16.8,4.4,14.2,3.8,"#525a66","#ffd694"],[2.2,-18.8,5.2,18.4,4.6,"#394452","#ffca80"],[8.8,-16.2,4.2,13.6,4,"#4c5460","#9ae3eb"],[15.2,-14.8,4.8,15.8,4.2,"#414a57","#ffcf87"],[-18.5,-6.5,4,11.4,4.4,"#4b5461","#ffd694"],[-19.2,1.8,4.4,10.2,4.2,"#3f4956","#8ce4f2"],[18.8,-5.5,4.2,12.6,4.4,"#454f5d","#ffcf87"],[19.6,3.8,4.6,10.6,4,"#3b4654","#ffd694"]].forEach(([O,W,Q,xe,fe,Pe,Be],he)=>{T.box(O,n+xe/2,W,Q,xe,fe,Pe,0,!0),T.box(O,n+xe+.45,W,Q*.62,.9,fe*.62,"#313942",0,!0),xe>14&&(T.box(O,n+xe+1.6,W,.14,1.8,.14,"#6f7782"),T.glowBox(O,n+xe+2.55,W,.24,.24,.24,"#ff6b6b"));let me=Math.floor(Q/.85),M=Math.floor((xe-1.5)/1.15);for(let ge=0;ge<M;ge++)for(let ee=0;ee<me;ee++){if(i(ee+he*7,ge,19)<.42)continue;let De=O-Q/2+.55+ee*.82,Ce=n+1.4+ge*1.12,Ke=i(ee,ge+he,23)>.78?"#9be4f0":Be;W<-8&&T.glowBox(De,Ce,W+fe/2+.04,.42,.58,.06,Ke),O<-12&&T.glowBox(O+Q/2+.04,Ce,W-fe/2+.55+ee%4*.9,.06,.58,.42,Ke),O>12&&T.glowBox(O-Q/2-.04,Ce,W-fe/2+.55+ee%4*.9,.06,.58,.42,Ke)}(he===1||he===3)&&T.glowBox(O,n+xe+.6,W+fe/2+.08,Q*.55,.42,.08,he===1?"#ff8b7b":"#7ad8e8")});for(let[O,W]of[[-7.4,2.2],[-7.4,5.2],[7.4,4.4],[2.4,7.1]])T.box(O,n+.58,W,1.35,.52,.72,"#7e5d4b",0,!0),T.box(O,n+.98,W,1.15,.44,.54,"#587954",0,!0);T.windowBox(-3.6,-4.78,"#4b555c","#343c42","#597b58","#74926c","#e49d6a","#eed29d"),T.finalize({textureKey:"city",roughness:.88,metalness:.06,ambientParticleColor:"#ffd892",ambientAlwaysOn:!0})}{let G=function(L,C,z,O){let W=Math.floor(z/.65);for(let Q=0;Q<W;Q++)if(T.box(L,n+.32+Q*.65,C,.14,.61,.14,Q%2?"#698a52":"#77995e"),T.box(L,n+.62+Q*.65,C,.17,.05,.17,"#93ad76"),Q>W-4)for(let xe=0;xe<3;xe++){let fe=O+xe*2.1+Q;T.box(L+Math.cos(fe)*.36,n+.5+Q*.65,C+Math.sin(fe)*.36,.42,.08,.24,"#587d47",fe,!0)}},T=y("washitsu");T.foundationBorder("#9c9884","#8c8875");for(let L=0;L<11;L++){let C=7.4+L*.72,z=1.25+Math.sin(L*.55)*.9;T.box(C,n+.04,z,.58,.09,.48,L%2?"#8b8d83":"#7c7f76",L*.2,!0)}for(let L=3.5;L<=12.2;L+=.55)for(let C=6.2;C<=12.4;C+=.55){let z=Math.hypot((L-7.8)/4.2,(C-9.2)/3);if(!(z>1))if(z>.84){let O=.12+i(L,C,31)*.22;T.box(L,n+O/2,C,.54,O,.54,i(L,C)>.5?"#7a7d73":"#666b63",0,!0)}else T.box(L,n+.02,C,.55,.04,.55,z<.45?"#3d7474":"#4d8784"),i(L,C,33)>.86&&T.box(L,n+.055,C,.34,.03,.34,"#5d8754"),i(L,C,34)>.94&&T.box(L,n+.045,C,.22,.03,.09,"#e67c4c",.5)}for(let L=0;L<42;L++){let C=L%3,z,O;C===0?(z=-10.5+i(L,61)*20,O=-7.2-i(L,62)*5.2):C===1?(z=-8.2-i(L,63)*4.6,O=-6+i(L,64)*13.5):(z=8.4+i(L,65)*4.5,O=-6.5+i(L,66)*5.2),G(z,O,3.8+i(L,67)*3.4,L)}T.broadleafTree(-6.5,-8.5,1.15,77,["#b84f3f","#cc664b","#d98256","#9a3f34","#e3a26b"],"#5c4738"),T.broadleafTree(8.2,-6.5,1,78,["#b84f3f","#cc664b","#d98256","#9a3f34"],"#5c4738");for(let[L,C,z]of[[-4.2,7.8,1.2],[-1.2,8.6,.9],[-8.2,7.2,1.15]])T.box(L,n+.36*z,C,1.1*z,.72*z,.85*z,"#777b74",.3,!0),T.box(L,n+.74*z,C,.8*z,.1*z,.62*z,"#658454",.3,!0);T.stoneLantern(-7.4,5.4,1,"#a8a596","#7a796e","#ffdfa2"),T.stoneLantern(8.5,4.6,.95,"#a8a596","#7a796e","#ffdfa2"),T.stoneLantern(-4.5,-5.6,.88,"#a8a596","#7a796e","#ffdfa2"),T.windowBox(-3.6,-4.78,"#997b4c","#635b3e","#5d7d4b","#789862","#d96b52","#f0e2c6"),T.finalize({textureKey:"zen",roughness:.97,ambientParticleColor:"#ffdca0"})}let x=document.createElement("canvas");x.width=x.height=32;let S=x.getContext("2d"),E=S.createRadialGradient(16,16,1,16,16,15);E.addColorStop(0,"rgba(255,255,230,1)"),E.addColorStop(.35,"rgba(255,232,165,0.85)"),E.addColorStop(1,"rgba(255,210,120,0)"),S.fillStyle=E,S.fillRect(0,0,32,32);let R=new ri(x),P=42,V=[],X=[],H=new Float32Array(P*3);for(let T=0;T<P;T++){let G,L,C=0;do T<18?(G=-7+i(T,61)*16,L=6.8+i(T,62)*6.5):T<30?(G=-14+i(T,63)*8,L=-5+i(T,64)*14):(G=6.8+i(T,65)*8,L=-4+i(T,66)*14);while(s(G,L,.5)&&++C<12);let z=n+.55+i(T,67)*2.1;V.push(G,z,L),X.push(i(T,68)*Math.PI*2),H[T*3]=G,H[T*3+1]=z,H[T*3+2]=L}let se=new St;se.setAttribute("position",new on(H,3).setUsage(dc));let oe=new Ts({map:R,color:"#ffe4a2",size:4.8,sizeAttenuation:!1,transparent:!0,opacity:.85,depthWrite:!1,blending:Za}),te=new Ca(se,oe);te.frustumCulled=!1,te.visible=!1,e.add(te),m.push(se),v.push(oe);function ne(T){let G=se.attributes.position;for(let L=0;L<P;L++){let C=X[L];G.array[L*3]=V[L*3]+Math.sin(T*.45+C)*.42,G.array[L*3+1]=V[L*3+1]+Math.sin(T*.62+C*1.7)*.24,G.array[L*3+2]=V[L*3+2]+Math.cos(T*.38+C*2.1)*.36}G.needsUpdate=!0,oe.opacity=.55+.3*Math.sin(T*.9)}let j=new hn({color:"#9fbcc3",roughness:.18,metalness:.12,transparent:!0,opacity:.58});v.push(j);let J=[[8.7,6.5,.8],[10.9,10.6,1.1],[7.7,3.2,.7],[-8.2,6.9,1.2],[3.8,8.8,.7],[-4.5,9.8,.9],[11.8,-.7,1.1],[-8.7,-.9,.8],[13,7.6,.9],[5.2,-6.2,.8]],ie=new Tn(t,j,J.length*3),re=new st,Se=new N,we=new Bt,ze=new N;J.forEach(([T,G,L],C)=>{for(let z=0;z<3;z++)Se.set(T+(z-1)*.23*L,n+(o(T,G)<1.1?.064:.015),G+z%2*.17),ze.set((z===1?1.25:.75)*L,.01,.42*L),ie.setMatrixAt(C*3+z,re.compose(Se,we,ze))}),ie.computeBoundingSphere(),e.add(ie),g.push(ie);let Ae=new hn({color:"#edf3ef",roughness:1,transparent:!0,opacity:.66,depthWrite:!1});v.push(Ae);let Ie=new Tn(t,Ae,15);[[-19,9.5,-27],[-3,12.5,-32],[19,11.2,-25]].forEach(([T,G,L],C)=>{for(let z=0;z<5;z++)Se.set(T+(z-2)*1.7,G+z%2*.55,L+z%3*.35),ze.set(3,.58+i(C,z)*.55,1.6),Ie.setMatrixAt(C*5+z,re.compose(Se,we,ze))}),Ie.computeBoundingSphere(),e.add(Ie),g.push(Ie);let ve=new cn({color:"#d6e7f1",transparent:!0,opacity:.6,depthWrite:!1});v.push(ve);let Ee=new Tn(t,ve,640);Ee.instanceMatrix.setUsage(dc),Ee.frustumCulled=!1,e.add(Ee),g.push(Ee);let Le=new N(0,0,1),Ye=Array.from({length:640},(T,G)=>{let L,C,z=0;do L=i(G,11,z)*40-19,C=i(G,12,z++)*40-17;while(s(L,C,.8));return{x:L,z:C,phase:i(G,13),size:.72+i(G,14)*.55}}),We="clear",U="day",D="classic",$=0,B=0;function w(T){let G=We==="snow",L=G?.072:We==="storm"?.94:.66;for(let C=0;C<B;C++){let z=Ye[C],O=(z.phase+T*L)%1;Se.set(z.x+(G?Math.sin(T*.8+C)*.18:0),n+.06+(1-O)*10.5,z.z),G?(ze.setScalar(.066*z.size),we.setFromAxisAngle(Gt.DEFAULT_UP,T*.22+C)):(ze.set(.017,(We==="storm"?.52:.35)*z.size,.017),we.setFromAxisAngle(Le,-.12)),Ee.setMatrixAt(C,re.compose(Se,we,ze))}Ee.instanceMatrix.needsUpdate=!0}function k(){let T=u[D]||u.classic;for(let[xe,fe]of Object.entries(u))fe.group.visible=xe===D;let G=We==="snow",L=["rain","storm"].includes(We),C=U==="night",z=["cloudy","rain","storm","snow","mist"].includes(We),O=D==="coastal";p.map=G&&!O?l.snow:l[T.textureKey],p.needsUpdate=!0;let W=G&&!O?"#ffffff":L?O?"#6e9aa8":"#a2b9bc":We==="mist"?"#d7e2db":C&&O?"#436d82":C&&D==="french"?"#6c7f99":C&&D==="loft"?"#4e5663":U==="dusk"&&O?"#d8b4a6":T.lawnTint;p.color.set(W),p.roughness=O?.28:L?.62:.96,p.metalness=O?.16:0,T.material.color.set(L?"#b5c5ca":"#ffffff"),T.material.roughness=L?Math.max(.55,T.roughness-.25):T.roughness,T.snowCaps.visible=G,T.glowMesh&&(T.glowMesh.visible=!0,T.glowMat.color.set(C?"#ffffff":U==="dusk"?"#fff2d8":"#d8d0be")),ie.visible=L&&!O,Ie.visible=z||D==="french",Ae.color.set(C?"#748799":We==="storm"?"#8c9da7":We==="rain"?"#becdd2":U==="dusk"?"#f2cfc0":"#e9f0f0"),Ae.opacity=We==="mist"?.35:.68,B=G?240:We==="storm"?640:L?430:0,Ee.visible=B>0,Ee.count=B,ve.color.set(G?"#ffffff":C?"#b1c5df":"#d6e7f1"),ve.opacity=G?.9:We==="storm"?.63:.5,oe.color.set(T.ambientParticleColor);let Q=!["rain","storm","snow"].includes(We);te.visible=Q&&(U==="night"||U==="dusk"||T.ambientAlwaysOn),T.animated.forEach(xe=>xe.update($,U)),w($)}function Y(T){let G=Object.hasOwn(u,T)?T:"classic";G===D&&u[G].group.visible||(D=G,k())}function K(T,G){We=T,U=G,k()}return Y("classic"),K("clear","day"),{setWorld:Y,setWeather:K,tick(T){let G=u[D]||u.classic,L=G.animated.length>0;return!B&&!te.visible&&!L?!1:($=T,B&&w(T),te.visible&&ne(T),L&&G.animated.forEach(C=>C.update(T,U)),!0)},getState(){let T=u[D]||u.classic;return{worldStyle:D,condition:We,light:U,particles:B,elapsed:$,snowCover:T.snowCaps.visible,wetGround:ie.visible,clouds:Ie.visible,fireflies:te.visible}},dispose(){e.removeFromParent(),g.forEach(T=>T.dispose()),m.forEach(T=>T.dispose()),v.forEach(T=>T.dispose()),Object.values(l).forEach(T=>T.dispose()),R.dispose()}}}window.PrivateCompanionHomeRoom3D=(()=>{let r=new WeakMap,e={calendar:{label:"生活日历",target:[-5.2,2.4,-2.85],position:[3.1,5.4,5.1],fov:38},desk:{label:"日记与便签",target:[-3.7,1.35,-3.25],position:[2.3,6.4,4.5],fov:36},shelf:{label:"故事书架",target:[-4.8,1.9,-.3],position:[5.2,4.8,5.8],fov:37},wardrobe:{label:"衣柜 · 房间陈设",target:[-.25,1.9,-3.2],position:[5.9,4.6,6.3],fov:35},bed:{label:"睡眠与梦境",target:[3.8,.9,-2.1],position:[9.4,6.1,6.3],fov:36},radio:{label:"见闻电台",target:[-4.8,1,3],position:[3.5,3.7,9.1],fov:36},garden:{label:"慢慢成长",target:[4.35,.9,2.95],position:[9.9,4.3,10.5],fov:34},game:{label:"游戏伴侣"},image:{label:"生图工作室"},together:{label:"在一起"},art:{label:"相框与照片"}},t={desk:"窗边书桌",shelf:"故事书架",wardrobe:"衣柜",bed:"床与床头柜",radio:"电台边柜",garden:"阶梯花架",lounge:"阅读扶手椅",readingLamp:"落地灯",stool:"茶几",rug:"编织地毯",game:"游戏伴侣摆件",image:"生图摆件",together:"在一起电话",calendar:"生活挂历",art:"植物装饰画"},n={floor:"地面","back-wall":"后墙","left-wall":"左墙"},i=["back-wall","left-wall"],s={game:{chess:{label:"双人棋桌",surfaces:["floor"]},arcade:{label:"复古街机",surfaces:["floor"]},console:{label:"游戏挂屏",surfaces:i}},image:{camera:{label:"三脚架相机",surfaces:["floor"]},instant:{label:"拍立得展台",surfaces:["floor"]},gallery:{label:"照片挂架",surfaces:i}},together:{telephone:{label:"转盘电话柜",surfaces:["floor"]},wallPhone:{label:"壁挂电话",surfaces:i}},calendar:{calendar:{label:"便签挂历",surfaces:i}},art:{botanical:{label:"植物装饰画",surfaces:i}}},a={minX:-5.62,maxX:5.7,minZ:-4.2,maxZ:5.46,wallTop:4.65,lowWallTop:1.38,lowWallStart:1.62},o={oak:"原木日常",cream:"奶油轻居",ink:"墨蓝复古",rattan:"日式藤编",walnut:"胡桃中古",loft:"工业拼搭",blossom:"樱色软装",nordic:"北欧几何"},c=["rattan","walnut","loft","blossom","nordic"],l={oak:{note:"温润木色，保留熟悉的生活细节。",models:["窗边书桌","开放书格","竖纹双门柜","木栅床头","格栅边柜","阶梯花架","木扶手阅读椅","层叠布罩灯","编织矮茶几","椭圆织毯"]},cream:{note:"浅色饰面和饱满轮廓，让角落更轻柔。",models:["浅木围板桌","错层书格","素面分格柜","三片软包床","浅色包边柜","方柱花架","宽背软椅","方筒布罩灯","奶油方茶几","奶油条纹毯"]},ink:{note:"墨蓝与暖金搭配，点缀复古细节。",models:["金属脚书桌","宽檐书架","金线双门柜","高低栅格床","金边收音柜","细脚花架","高背阅读椅","锥形层叠灯","金脚矮茶几","墨蓝织纹毯"]},rattan:{note:"藤编格纹、细木框与竹节，带一点庭院气息。",models:["藤编抽屉桌","竹格书架","藤芯双门柜","藤编屏风床","藤编唱片柜","竹梯花架","藤背休闲椅","竹编宫灯","藤篮茶几","流苏草编毯"]},walnut:{note:"深胡桃木、收分轮廓与黄铜细节。",models:["中古抽屉桌","胡桃分区书柜","雕线胡桃柜","翼形木床头","中古唱片柜","胡桃陈列架","翼背扶手椅","琥珀蘑菇灯","椭圆双层茶几","复古菱格毯"]},loft:{note:"深色钢架、铆钉和交叉撑杆，结构清晰利落。",models:["钢架工作台","桁架书架","铆钉储物柜","钢管框架床","铆钉器材柜","钢架植物台","悬臂框架椅","折臂工作灯","框架工作茶几","拼色方格毯"]},blossom:{note:"樱粉软包和花瓣轮廓，配细腻的浅色木脚。",models:["樱色梳写桌","花瓣顶书架","花窗双门柜","花瓣软包床","樱色小边柜","花瓣托盘架","花瓣靠背椅","花苞落地灯","花瓣矮茶几","花朵簇绒毯"]},nordic:{note:"白蜡木、鼠尾草绿与几何留白，轻巧通透。",models:["A 字支架桌","几何分格架","拼色平板柜","三角拼板床","拼色几何柜","A 字花架","曲折木框椅","三脚折纸灯","三脚圆茶几","山形几何毯"]}},h={rattan:{wood:"#c6a16a",edge:"#876740",accent:"#91a17a",dark:"#596d4d",light:"#d6c59c",fabric:"#cebd92",rose:"#bb8765"},walnut:{wood:"#76503a",edge:"#48392f",accent:"#986d46",dark:"#543f34",light:"#bc996e",fabric:"#ad845f",rose:"#ba754f"},loft:{wood:"#b49a79",edge:"#38474b",accent:"#72888a",dark:"#3d4e53",light:"#b9c4bf",fabric:"#879b98",rose:"#c68a50"},blossom:{wood:"#e1c6ad",edge:"#a48a7c",accent:"#d99fac",dark:"#9c6c80",light:"#f0c6ca",fabric:"#e2b1bc",rose:"#b66f89"},nordic:{wood:"#d0b589",edge:"#786e58",accent:"#91a899",dark:"#506b63",light:"#e4eadb",fabric:"#adc2b5",rose:"#c29569"}},p={classic:{label:"庭院木屋",materials:"暖木花庭 · 绿墙裙 · 拱架花园",note:"熟悉且温润的原木小屋，窗外是花架、石径与漫步的阳光。"},cabin:{label:"森林木屋",materials:"深林松杉 · 溪流木桥 · 外露木梁",note:"隐于深林松涛与林间溪畔的横木小屋，窗外萤光点点。"},coastal:{label:"海畔白屋",materials:"碧海灯塔 · 蓝白花砖 · 百叶窗",note:"坐落海角崖边的白灰小屋，木栈道伸向波光粼粼的海面。"},french:{label:"山巅云屋",materials:"雪峰云海 · 法式线板 · 拱形观景窗",note:"云海与雪峰环绕的山巅石台，晨昏时分云雾在窗沿流动。"},loft:{label:"城市天台",materials:"天际霓虹 · 红砖水泥 · 钢格窗",note:"俯瞰城市天际线的红砖阁楼天台，夜幕降临时万家灯火亮起。"},washitsu:{label:"竹林和室",materials:"竹影红枫 · 枯山水 · 榻榻米纸窗",note:"细木格与柔光纸窗，窗外是竹林、锦鲤池与静谧枯山水。"}},f={washitsu:{wall:"#e4dfc9",lower:"#d2c9aa",wood:"#a88855",edge:"#635b3e",accent:"#788369",floor:["#b7b185","#c9c39a","#c1bb90"]},loft:{wall:"#b87961",lower:"#a96e57",wood:"#796955",edge:"#3e4b4d",accent:"#617a7b",floor:["#a7aaa1","#b5b7ad","#aeb2a7"]},french:{wall:"#f2e9da",lower:"#e4d8c2",wood:"#d9c7a9",edge:"#b59b77",accent:"#b8b89e",floor:["#bf9870","#d1b18b","#c8a67f"]},coastal:{wall:"#f0f0df",lower:"#e3e9dd",wood:"#5e8d9b",edge:"#3d6f84",accent:"#74a0ad",floor:["#e5dfc7","#f0e9d6","#dcd5bd"]},cabin:{wall:"#b28b60",lower:"#ac8056",wood:"#7e5c3f",edge:"#543f30",accent:"#799079",floor:["#b88d5f","#c6a173","#ac8257"]}},m={classic:{sky:{day:"#c7d6d8",dawn:"#e0dacc",dusk:"#dcc8bb",night:"#253a49"},fog:[27,66],hemiSky:{day:"#e9f2ef",dawn:"#e9e2d9",dusk:"#f0d8cf",night:"#b6cce6"},hemiGround:{day:"#6d5c46",dawn:"#6b5a4a",dusk:"#6d5347",night:"#4d4a55"},sunColor:{day:"#ffe0b0",dawn:"#ffd9a3",dusk:"#ffb27a",night:"#a8c9ef"},sunScale:1,hemiScale:1,fillTint:"#cfe3e7",rimTint:"#c9e0e2",lampBoost:1,exposure:1.05},cabin:{sky:{day:"#9cb5a4",dawn:"#c8d0be",dusk:"#9e9888",night:"#172926"},fog:[18,50],hemiSky:{day:"#c5ded0",dawn:"#dbe2ce",dusk:"#d7c9b3",night:"#82ada2"},hemiGround:{day:"#44543c",dawn:"#4c5640",dusk:"#4d4234",night:"#29352b"},sunColor:{day:"#f3e7b8",dawn:"#ffe0a8",dusk:"#f4a86e",night:"#8ec4be"},sunScale:.78,hemiScale:.94,fillTint:"#9ec2b0",rimTint:"#a8cfba",lampBoost:1.42,exposure:1.02},coastal:{sky:{day:"#a6d4e6",dawn:"#ebd9cd",dusk:"#e6b39c",night:"#1c334b"},fog:[30,82],hemiSky:{day:"#dff4ff",dawn:"#f7e5d9",dusk:"#f7cbb6",night:"#9ec4ea"},hemiGround:{day:"#528a99",dawn:"#6d7b85",dusk:"#78696c",night:"#2c4c63"},sunColor:{day:"#fff0cb",dawn:"#ffdbad",dusk:"#ff9b6b",night:"#b4d6ff"},sunScale:1.12,hemiScale:1.08,fillTint:"#a6dce8",rimTint:"#b8e6f2",lampBoost:.95,exposure:1.08},french:{sky:{day:"#bed4e8",dawn:"#e9d7d3",dusk:"#d8b4bc",night:"#1d2942"},fog:[24,72],hemiSky:{day:"#e8f2fc",dawn:"#f7e4df",dusk:"#efd0d5",night:"#a8bee6"},hemiGround:{day:"#8e99a6",dawn:"#8f848c",dusk:"#827380",night:"#434c63"},sunColor:{day:"#fff4dc",dawn:"#ffd8c2",dusk:"#ffab8f",night:"#bfd4ff"},sunScale:1.08,hemiScale:1.04,fillTint:"#d4e3f5",rimTint:"#e2eeff",lampBoost:1.08,exposure:1.07},loft:{sky:{day:"#aebbc7",dawn:"#d6c4c6",dusk:"#7e7894",night:"#141a2b"},fog:[22,64],hemiSky:{day:"#d4dde6",dawn:"#e8d6d8",dusk:"#b6a9cf",night:"#7b91c4"},hemiGround:{day:"#585a64",dawn:"#625861",dusk:"#5a4d5c",night:"#33364a"},sunColor:{day:"#ffe2be",dawn:"#ffcaa6",dusk:"#ff8c66",night:"#8cb3e8"},sunScale:.9,hemiScale:.9,fillTint:"#9eb8d6",rimTint:"#c8a3d9",lampBoost:1.55,exposure:1.04},washitsu:{sky:{day:"#c7d4ca",dawn:"#e3dcd0",dusk:"#d5bfb2",night:"#1e2e30"},fog:[20,56],hemiSky:{day:"#e5eee6",dawn:"#eee6d8",dusk:"#ebd4c4",night:"#9ab8be"},hemiGround:{day:"#6b6e56",dawn:"#6c6554",dusk:"#67574c",night:"#3b4340"},sunColor:{day:"#ffebc4",dawn:"#ffe0b2",dusk:"#f5aa7a",night:"#a6c5cc"},sunScale:.88,hemiScale:1,fillTint:"#c8ddd2",rimTint:"#d5e4d8",lampBoost:1.25,exposure:1.03}},v={classic:"classic",courtyard:"classic",forest:"cabin",cabin:"cabin",ocean:"coastal",sea:"coastal",coastal:"coastal",mountain:"french",peak:"french",french:"french",city:"loft",urban:"loft",loft:"loft",zen:"washitsu",bamboo:"washitsu",washitsu:"washitsu"},g=[{label:"01 / 越过屋檐 · 俯冲入室",points:[[0,18,2],[8,12,10],[6,5,9],[3,2.3,6]],target:[0,1,0],fovs:[46,50,58,64],bank:-.075,seconds:8},{label:"02 / 桌沿掠影 · 推拉变焦",anchor:"desk",points:[[2.5,2.1,4.1],[1.2,1.8,2.8],[-.4,2.7,2.9],[-1,4.6,4.8]],target:[0,1.4,.1],fovs:[50,67,54,36],bank:.045,seconds:8},{label:"03 / 书脊长廊 · 横移掠过",anchor:"shelf",points:[[-2,2.5,4.3],[0,1.85,3.6],[2.3,2.7,4]],target:[0,1.8,.1],fovs:[55,62,49],bank:-.035,seconds:7},{label:"04 / 床边低语 · 半环绕升起",anchor:"bed",points:[[-.7,1.8,4.5],[-1.8,2.4,4.1],[-2.8,3.5,2.8],[-1.6,6,1.4]],target:[0,.9,0],fovs:[59,57,50,43],bank:.075,seconds:8},{label:"05 / 叶间光线 · 低位环绕",anchor:"garden",points:[[2.4,1.7,3],[0,2.1,3.1],[-2.1,1.7,2.2],[-2.4,3.8,.6]],target:[0,1,0],fovs:[55,63,58,46],bank:-.06,seconds:7},{label:"06 / 一间生活 · 螺旋拉远",points:[[0,8,7],[-1,16,9],[12.9,12.2,15.1]],target:[0,1.15,0],fovs:[48,44,38],bank:.06,seconds:7}],u={wood:"#a9764f",edge:"#77553f",oak:"#d0a574",cream:"#f5ecd9",paper:"#fff6df",teal:"#507e7a",tealLight:"#7bada1",tealDark:"#365f5a",rose:"#c47465",mustard:"#d6ad55",navy:"#435369",green:"#657c45",leaf:"#91a768",dark:"#303e3d",brass:"#d7b66c"},y=(S,E,R)=>{let P=parseInt(S.slice(1),16),V=parseInt(E.slice(1),16),X=H=>Math.round((P>>H&255)*(1-R)+(V>>H&255)*R);return"#"+(X(16)<<16|X(8)<<8|X(0)).toString(16).padStart(6,"0")};function x(S,E={}){var Cd;let R=structuredClone(e),P={...t},V=structuredClone(s),X=new Map,H={},se=new Set,oe=new Set,te=null,ne=new ba,j=new an(38,1,.1,110),J=new vc({antialias:!0,powerPreference:"high-performance"});J.setPixelRatio(Math.min(window.devicePixelRatio||1,1.65)),J.shadowMap.enabled=!0,J.shadowMap.type=Dr,J.shadowMap.autoUpdate=!1,J.shadowMap.needsUpdate=!0,J.outputColorSpace=Xt,J.toneMapping=Ja,J.toneMappingExposure=1.05,S.replaceChildren(J.domElement),J.domElement.setAttribute("aria-label","体素小屋。可拖动旋转、滚轮缩放，也可使用下方家具按钮浏览内容。");let ie=new Mc(j,J.domElement);ie.enableDamping=!0,ie.dampingFactor=.085,ie.enablePan=!1,ie.minDistance=1.5,ie.maxDistance=36,ie.minPolarAngle=.06,ie.maxPolarAngle=Math.PI*.49,ie.minAzimuthAngle=-.17,ie.maxAzimuthAngle=Math.PI*.59;let re=new jn(1,1,1),Se=new hn({roughness:.94,metalness:.02}),we=document.createElement("canvas");we.width=we.height=128;let ze=we.getContext("2d"),Ae=ze.createRadialGradient(64,64,5,64,64,64);Ae.addColorStop(0,"rgba(0,0,0,.58)"),Ae.addColorStop(.52,"rgba(0,0,0,.30)"),Ae.addColorStop(.82,"rgba(0,0,0,.08)"),Ae.addColorStop(1,"rgba(0,0,0,0)"),ze.fillStyle=Ae,ze.fillRect(0,0,128,128);let Ie=new ri(we),ve=new cn({map:Ie,color:"#2c2118",transparent:!0,opacity:.26,depthWrite:!1,toneMapped:!1}),Ee=new vn(1,1),Le=new sn(new N(0,-1,0),-1.05),Ye=J.clippingPlanes.slice(),We=J.shadowMap.enabled,U=1,D=new Map,$=[],B={},w=[],k={},Y={},K={},T={},G=new st,L=new Bt,C=new N,z=new N,O=new ke,W=0;function Q(d,_=0,A=0,b=0,I=0,le=ne){let Z=new Kt;return Z.position.set(_,A,b),Z.rotation.y=I,Z.userData.station=d,le.add(Z),Z}function xe(d){let _=d.userData.station,A=Q(_,0,0,0,0,d);return(Y[_]||(Y[_]=[])).push(A),A}let fe=Q(""),Pe=Q(""),Be=Q(""),he=Q(""),me=Q("");function M(d,_,A,b,I,le,Z,ue=u.oak,ye=0,Ue=0){D.has(d)||D.set(d,[]),D.get(d).push({x:_,y:A,z:b,w:I,h:le,d:Z,tint:ue,ry:ye,rz:Ue}),W++}function ge(d,_,A,b,I,le){M(d,_,A,b,I,I,I,le)}function ee(d,_,A,b,I=1){for(let le=0;le<4;le++)M(d,_,A+(.08+le*.13)*I,b,(.36+le*.055)*I,.13*I,(.36+le*.055)*I,le===3?u.cream:u.rose);M(d,_,A+.48*I,b,.37*I,.055*I,.37*I,u.edge);for(let le=0;le<7;le++){let Z=le*2.4,ue=.72+le%3*.22;for(let ye=0;ye<5;ye++){let Ue=ye/4;ge(d,_+Math.cos(Z)*Ue*.34*I,A+(.48+ue*Ue*.64)*I,b+Math.sin(Z)*Ue*.34*I,.105*I,u.tealDark)}for(let ye=-1;ye<=1;ye++)for(let Ue=0;Ue<3;Ue++)ge(d,_+(Math.cos(Z)*.34+ye*.1)*I,A+(.48+ue*.64+Ue*.085)*I,b+(Math.sin(Z)*.34+Ue*.025)*I,.13*I,(le+Ue)%2?u.green:u.leaf)}}function De(d,_,A,b,I,le=.64,Z=.16){M(d,_,A+le/2,b,Z,le,.43,I),M(d,_,A+le/2,b+.221,Z*.62,.024,.012,u.brass),M(d,_,A+le-.09,b+.221,Z*.72,.022,.012,u.paper),M(d,_,A+le-.015,b,Z*.7,.018,.38,u.paper)}function Ce(d,_,A,b,I=1){M(d,_,A+.06*I,b,.48*I,.12*I,.48*I,u.brass),M(d,_,A+.4*I,b,.075*I,.65*I,.075*I,u.edge);for(let le=0;le<5;le++){let Z=(.68-le*.075)*I;M(d,_,A+(.6+le*.085)*I,b,Z,.087*I,Z,le%2?"#e8ca87":"#f9dc9f")}}function Ke(d,_,A=u.cream,b=u.teal,I=u.oak){M(d,0,1.43,_+.06,3.96,.07,.075,I);for(let le of[-1,1])for(let Z=0;Z<5;Z++)ge(d,le*(1.2+Z*.2),1.43,_+.12,.045,u.brass);for(let le of[-1,1]){for(let Z=0;Z<3;Z++){let ue=le*(1.31+Z*.145);M(d,ue,-.02,_+.13+Z%2*.012,.145,2.52-Z*.035,.072,Z%2?A:u.paper),M(d,ue-.078,-.62,_+.17,.022,2.4,.012,y(u.edge,A,.35))}M(d,le*1.43,-1.3,_+.15,.53,.065,.1,A),M(d,le*1.43,-1.34,_+.16,.55,.04,.11,y(u.edge,A,.3)),M(d,le*1.43,-.55,_+.17,.47,.075,.13,b),M(d,le*1.43,-.51,_+.2,.13,.16,.04,u.brass)}}M(fe,0,-.43,.6,12.05,.54,10.45,u.tealDark),M(fe,0,-.14,.6,12.14,.13,10.52,u.edge);for(let d=0;d<34;d++)for(let _=0;_<9;_++){let A=-5.98+_*1.51+(d%2?-.74:0),b=Math.max(-5.98,A),I=Math.min(5.98,A+1.49);if(I<=b)continue;let le=-4.43+d*.3,Z=["#cda374","#d6af82","#c49b6d","#dfbb90","#d1a97a"][(d*3+_)%5];M(Pe,(b+I)/2,-.043,le,I-b,.065,.286,Z);for(let ue=0;ue<3;ue++)M(Pe,(b+I)/2+(ue-1)*.07,-.008,le+(ue-1)*.063,(I-b)*.68,.003,.008,d%2?"#bb9365":"#c59e72")}for(let d=-5.7;d<5.9;d+=.3)for(let _=.16;_<4.8;_+=.3)d>-5.2&&d<-1.95&&_>1.6&&_<4.25||M(Be,d,_,-4.47,.294,.294,.22,_<1.45?"#7d9b90":["#e6dfca","#eae3d0","#e2dbc7"][Math.abs(Math.round(d*10+_*10))%3]);for(let d=-4.4;d<5.7;d+=.3)for(let _=.15;_<(d>1.7?1.48:4.8);_+=.3)M(he,-5.9,_,d,.22,.293,.294,_<1.5?"#76948c":"#e5dfcd");for(let d of[.18,1.48,4.8])M(Be,0,d,-4.29,12,.1,.13,d===4.8?u.cream:u.tealDark);for(let d of[.18,1.48])M(he,-5.72,d,.6,.13,.1,10.2,u.tealDark);M(he,-5.88,4.8,-1.35,.33,.12,6.1,u.cream);for(let d=-5.5;d<5.8;d+=.55)M(Be,d,.83,-4.27,.05,1.16,.08,"#adc0aa");for(let d=-4.2;d<5.7;d+=.55)M(he,-5.74,.83,d,.08,1.16,.05,"#adc0aa");let Je=Q("",-3.6,2.94,-4.45),je=Q("",0,0,0,0,Je);for(let d of[-1.65,0,1.65])M(je,d,0,.06,.11,2.83,.23,u.cream);for(let d of[-1.39,0,1.39])M(je,0,d,.07,3.43,.11,.24,u.cream);M(je,0,-1.48,.29,3.7,.14,.68,u.oak);for(let d of[-1,1])for(let _=0;_<6;_++)M(je,d*(1.46+_*.085),-.02,.3+_%2*.065,.095,3.1-_*.025,.12,_%2?"#ecd8b8":"#fff0d0");Ke(je,.34,u.cream,u.teal,u.oak),ee(Je,-1.05,-1.4,.33,.48);let Ne=Q("",0,0,0,0,Je);M(Ne,.7,-1.38,.41,.16,.3,.12,u.brass),M(Ne,.7,-1.24,.41,.1,.06,.1,u.mustard),M(Ne,.94,-1.38,.41,.06,.22,.06,u.cream),M(Ne,1.18,-1.39,.41,.32,.035,.23,u.teal),M(Ne,1.18,-1.355,.41,.29,.03,.2,u.rose),M(Ne,1.18,-1.32,.41,.24,.03,.16,u.paper);let nt=new hn({color:"#d7e9e7",roughness:.16,metalness:0,transparent:!0,opacity:.13,depthWrite:!1,side:En}),yt=new vn(3.16,2.62),q=new Et(yt,nt);q.position.set(-3.6,2.93,-4.415),q.renderOrder=0,ne.add(q);for(let[d,_,A,b,I,le,Z]of[[0,.075,-4.325,11.86,.13,.07,u.cream],[-5.755,.075,.62,.07,.13,10.02,u.cream],[0,.148,-4.325,11.86,.022,.082,u.edge],[-5.755,.148,.62,.082,.022,10.02,u.edge],[0,4.735,-4.325,11.86,.055,.06,u.cream],[-5.76,2.4,-4.33,.055,4.55,.055,u.cream],[-5.17,2.93,-4.47,.072,2.7,.22,u.cream],[-1.98,2.93,-4.47,.072,2.7,.22,u.cream],[-3.575,4.235,-4.47,3.26,.075,.22,u.cream],[-3.575,1.61,-4.47,3.26,.075,.22,u.cream],[-5.16,2.93,-4.345,.018,2.74,.028,u.edge],[-1.99,2.93,-4.345,.018,2.74,.028,u.edge],[-3.575,4.242,-4.345,3.26,.018,.028,u.edge]])M(me,d,_,A,b,I,le,Z);let qe={x:1.38,y:3.36,z:-4.31,cell:.071};for(let d=-3;d<=3;d++)for(let _=-3;_<=3;_++){let A=Math.hypot(_,d);A>3.22||M(me,qe.x+_*qe.cell,qe.y+d*qe.cell,qe.z,qe.cell-.006,qe.cell-.006,.035,A>2.55?u.edge:u.paper)}M(me,qe.x,qe.y+.035,qe.z+.022,.022,.16,.018,u.dark),M(me,qe.x+.045,qe.y,qe.z+.022,.12,.022,.018,u.dark),ge(me,qe.x,qe.y,qe.z+.028,.034,u.brass),M(me,qe.x,qe.y+.25,qe.z,.026,.09,.022,u.brass);for(let d of[2,4.35,5.28]){M(me,-5.66,1.61,d,.18,.16,.18,"#b46f4f"),M(me,-5.66,1.7,d,.14,.022,.14,"#594331");for(let _=0;_<3;_++)M(me,-5.7+_*.04,1.77+_*.055,d,.075,.1,.075,_%2?u.leaf:u.tealLight);d!==4.35&&M(me,-5.66,1.87,d,.1,.05,.1,u.rose)}for(let[d,_]of[[2.22,.92],[5.12,.84]]){M(me,d,3.2,-4.3,_,.055,.2,u.oak);for(let A of[-1,1])M(me,d+A*(_/2-.07),3.08,-4.33,.05,.18,.06,u.edge)}for(let d=0;d<4;d++)M(me,1.88+d*.16,3.31,-4.3,.13,.17-d*.018,.18,[u.teal,u.rose,u.mustard,u.paper][d]);M(me,2.5,3.35,-4.3,.16,.14,.16,"#b46f4f"),M(me,2.5,3.45,-4.3,.22,.05,.22,u.leaf),M(me,5.12,3.31,-4.3,.22,.16,.18,"#b46f4f"),M(me,5.12,3.43,-4.3,.26,.07,.22,u.leaf),M(me,5.36,3.32,-4.3,.11,.22,.12,u.brass),M(me,5.36,3.45,-4.3,.08,.08,.08,u.mustard);let Oe=B.desk=Q("desk",-3.7,0,-3.45),Qe=xe(Oe);M(Oe,0,1.22,0,3.3,.18,1.3,u.oak);for(let d=0;d<11;d++)M(Oe,-1.49+d*.298,1.318,0,.285,.017,1.27,d%3?"#d8b68b":"#c9a779");for(let d of[-1.42,1.42])for(let _ of[-.46,.46])M(Qe,d,.58,_,.14,1.16,.14,u.edge);for(let d=0;d<3;d++)M(Qe,1.03,.43+d*.25,.02,.68,.235,1,u.teal),M(Qe,1.03,.43+d*.25,.55,.19,.05,.06,u.brass);for(let d of[-1,1]){M(Oe,d*.3-.12,1.365,.07,.59,.055,.76,u.rose,0,d*.045);for(let _=0;_<4;_++)M(Oe,d*.29-.12,1.397+_*.009,.07,.54,.008,.7,u.paper,0,d*.035);for(let _=0;_<6;_++)M(Oe,d*.29-.12,1.442,-.17+_*.073,.38,.007,.012,"#acb3a0")}M(Oe,.5,1.37,.28,.04,.045,.57,u.navy,-.26),Ce(Oe,-1.13,1.32,-.24,.76),M(Oe,.77,1.49,-.36,.26,.34,.26,u.cream);for(let d=0;d<4;d++)M(Oe,.69+d*.05,1.76,-.37,.027,.41,.03,d%2?u.rose:u.navy,0,(d-1)*.1);for(let d=0;d<3;d++)M(Oe,.34+d*.02,1.35+d*.028,-.42,.3,.023,.23,[u.mustard,u.rose,u.tealLight][d]);M(Oe,-1.34,1.42,.32,.17,.22,.17,u.cream),M(Oe,-1.45,1.47,.32,.05,.08,.05,u.cream),M(Oe,-1.34,1.53,.32,.12,.02,.12,u.edge),M(Oe,1.34,1.38,-.5,.38,.055,.29,u.teal),M(Oe,1.34,1.435,-.5,.34,.045,.26,u.rose),M(Oe,1.34,1.49,-.5,.28,.045,.22,u.paper);let wn=Q("desk",-.4,0,1.27,-.18,Oe);M(wn,0,.7,0,.87,.17,.82,u.teal);for(let d of[-.34,.34])for(let _ of[-.29,.29])M(wn,d,.34,_,.1,.68,.1,u.edge);for(let d=0;d<5;d++)M(wn,-.35+d*.176,1.18,.33,.12,.86,.12,u.teal);M(wn,0,1.59,.33,.9,.13,.16,u.tealDark),M(wn,0,.8,.02,.78,.065,.72,u.rose);let Dn=B.calendar=Q("calendar",-5.69,2.84,-2.98,Math.PI/2);M(Dn,0,0,0,1.9,1.72,.09,u.edge),M(Dn,0,0,.06,1.72,1.54,.08,"#b59265"),M(Dn,-.25,.11,.12,1,1.23,.07,u.paper),M(Dn,-.25,.63,.17,1.01,.23,.05,u.rose);for(let d=0;d<7;d++)for(let _=0;_<4;_++)M(Dn,-.65+d*.128,.34-_*.18,.17,.065,.064,.012,_===1&&d===3?u.rose:"#b6b5a2");for(let d=0;d<3;d++)M(Dn,.58,.46-d*.43,.16,.4,.32,.03,[u.mustard,u.tealLight,u.cream][d],0,(d-1)*.08),ge(Dn,.58,.56-d*.43,.19,.043,u.rose);let Zn=B.shelf=Q("shelf",-5.19,0,-.1,Math.PI/2),gr=xe(Zn);M(gr,0,1.74,-.32,2.68,3.45,.09,u.edge);for(let d of[-1.37,1.37])M(gr,d,1.77,0,.16,3.54,.78,u.oak);for(let d=0;d<5;d++)if(M(Zn,0,.18+d*.84,0,2.9,.12,.85,u.oak),d!==4){for(let _=0;_<(d===2?6:11);_++)De(Zn,-1.17+_*.228,.245+d*.84,.035,[u.teal,u.rose,u.navy,u.mustard,u.cream][(_+d*2)%5],.49+(_*7+d)%4*.065,.15+_%2*.025);if(d===2){for(let _=0;_<3;_++)M(Zn,.7,1.99+_*.12,.1,.65,.1,.42,_%2?u.tealLight:u.cream);M(Zn,.7,2.42,.1,.35,.13,.3,u.brass)}}ee(Zn,.87,3.6,0,.68);for(let d=0;d<3;d++)M(Zn,-.67,3.67+d*.12,0,.66,.1,.47,[u.teal,u.paper,u.rose][d]);let Vt=B.wardrobe=Q("wardrobe",-.25,0,-3.63);M(Vt,0,1.68,-.39,2.55,3.25,.1,u.tealDark);for(let d of[-1.32,1.32])M(Vt,d,1.7,0,.16,3.4,1.05,u.teal);for(let d of[.2,3.4])M(Vt,0,d,0,2.85,.18,1.15,u.teal);M(Vt,0,2.83,0,2.53,.06,.06,u.brass);for(let d=0;d<6;d++){let _=-.91+d*.36;M(Vt,_,2.67,0,.04,.28,.04,u.brass);for(let A=0;A<9;A++)M(Vt,_,2.48-A*.125,.04,.21+(A<2?.1:A*.009),.122,.51,[u.rose,u.cream,u.navy,u.mustard,u.tealLight,u.paper][d])}for(let d of[-.67,.67])M(Vt,d,.51,0,1.15,.5,.89,u.oak),M(Vt,d,.54,.48,.26,.06,.06,u.brass);for(let d of[-1,1]){let _=Q("wardrobe",d*1.31,0,.56,0,Vt);w.push({group:_,side:d});let A=xe(_);M(A,-d*.647,1.84,0,1.28,2.92,.095,u.teal);for(let b=0;b<7;b++)M(A,-d*(.1+b*.175),1.84,.052,.025,2.68,.025,"#6b9890");for(let b of[.51,3.16])M(A,-d*.65,b,.075,1.12,.055,.035,u.tealLight);M(_,-d*1.15,1.74,.11,.054,.23,.07,u.brass)}for(let d=0;d<2;d++)M(Vt,-.48+d*1.1,3.7,-.05,.85,.43,.73,d?u.mustard:u.cream);M(Vt,.88,3.99,.18,.18,.16,.18,u.edge),M(Vt,.88,4.1,.18,.23,.045,.23,u.brass);for(let d=0;d<4;d++)M(Vt,.8+d*.055,4.15+d*.055,.18,.12,.1,.12,d%2?u.leaf:u.tealDark);for(let d=0;d<3;d++)M(Vt,.97,4.02-d*.12,.43,.055,.13,.055,d%2?u.leaf:u.tealLight);let Pt=B.bed=Q("bed",3.9,0,-2.2);for(let d of[-1.18,1.18])for(let _ of[-1.64,1.64])M(Pt,d,.21,_,.17,.43,.17,u.edge);M(Pt,0,.43,0,2.81,.24,3.78,u.oak);let ro=xe(Pt);for(let d=0;d<13;d++)M(ro,-1.27+d*.212,1.05,-1.86,.18,1.44-Math.abs(d-6)*.023,.15,u.teal);for(let d=0;d<4;d++)M(Pt,0,.57+d*.07,0,2.65-(d===3?.12:0),.072,3.59,d%2?u.paper:"#e1d5b9");for(let d=0;d<22;d++)for(let _=0;_<24;_++){let A=-1.32+d*.126,b=-.87+_*.115,I=.9-Math.max(0,Math.abs(A)-1.12)*1.5+Math.sin(d*.68)*.024;ge(Pt,A,I,b,.126,_%8===0||d%8===0?"#a9c3ac":(d+_)%3?"#6e9990":"#82a89a")}for(let d of[-.68,.67])for(let _=0;_<9;_++)for(let A=0;A<5;A++)ge(Pt,d-.44+_*.11,.96+(_>0&&_<8&&A>0&&A<4?.07:0),-1.51+A*.11,.118,d<0?u.paper:"#d6bd99");for(let d=0;d<24;d++)M(Pt,-1.39+d*.12,.94,1.39,.115,.055,.48,d%4<2?u.rose:"#dc9b79");for(let d=0;d<4;d++)M(Pt,-1.14+d*.57,1.01,.66,.54,.06,.48,d%2?u.rose:"#dc9b79");M(Pt,-1.16,1.055,1.36,.28,.035,.1,u.paper),M(Pt,-.12,1.055,1.36,.28,.035,.1,u.paper),M(Pt,.28,1.045,.68,.38,.045,.3,u.paper),M(Pt,.28,1.09,.68,.34,.025,.26,u.tealDark),M(Pt,.02,1.05,-1.42,.48,.3,.17,y(u.mustard,u.cream,.26)),M(Pt,.02,1.21,-1.4,.2,.045,.13,u.brass),M(Pt,-1.98,.5,-1.09,.75,.94,.85,u.oak),M(Pt,-1.98,.52,-.637,.57,.65,.07,u.cream),ge(Pt,-1.98,.59,-.576,.075,u.brass),Ce(Pt,-1.98,1.01,-1.09,.79);let so=xe(B.rug=Q("rug",-1.65,0,1.62));for(let d=-22;d<=22;d++)for(let _=-16;_<=16;_++){if((d/23)**2+(_/17)**2>1)continue;let A=(d/23)**2+(_/17)**2>.78;M(so,d*.115,.025,_*.115,.112,.036,.112,A?(d+_)%3?u.rose:u.mustard:(d+_)%4?"#e5d8b8":"#dbcaab")}let rn=B.radio=Q("radio",-5.12,0,3,Math.PI/2),_r=xe(rn);for(let d of[-.94,.94])for(let _ of[-.34,.34])M(_r,d,.17,_,.12,.34,.12,u.edge);M(_r,0,.64,0,2.34,.78,.95,u.oak);for(let d=0;d<16;d++)M(_r,-.97+d*.129,.65,.495,.06,.58,.07,u.edge);M(rn,-.35,1.37,-.02,1.3,.64,.5,u.tealDark),M(rn,-.55,1.38,.25,.69,.44,.035,u.cream);for(let d=0;d<7;d++)for(let _=0;_<4;_++)ge(rn,-.81+d*.088,1.24+_*.091,.278,.027,u.edge);M(rn,.055,1.45,.253,.24,.15,.04,u.mustard);for(let d of[-.01,.13])ge(rn,d,1.23,.29,.09,u.brass);M(rn,-.86,1.99,-.09,.027,.72,.027,u.brass,0,-.22);for(let d=0;d<4;d++)M(rn,.73,1.09+d*.075,.04,.49,.057,.51,d%2?u.paper:u.rose);ee(rn,.71,1.35,-.03,.47);let js=B.garden=Q("garden",4.4,0,3.4),Xs=xe(js);for(let d=0;d<3;d++){let _=.18+d*.26,A=-.62+d*.55;M(Xs,A,_,-.13,.62,.09,.8,u.oak);for(let b of[-.45,.17])M(Xs,A,_/2,b,.07,_,.07,u.edge);ee(js,A,_+.05,-.11,[.73,.92,1.15][d])}let un=xe(B.lounge=Q("lounge",-2.85,0,1.3,Math.PI/2));for(let d of[-.57,.57])for(let _ of[-.5,.5])M(un,d,.17,_,.13,.34,.13,u.edge);M(un,0,.46,0,1.44,.25,1.5,u.oak);for(let d=0;d<10;d++)for(let _=0;_<9;_++)ge(un,-.58+d*.13,.69+(d>0&&d<9&&_>0&&_<8?.04:0),-.48+_*.13,.132,(d+_)%4?"#c89669":"#d4a87a");for(let d=0;d<11;d++)M(un,-.65+d*.13,1.16,-.64,.125,1.03,.2,d%3?"#bd895d":"#c89669");for(let d of[-.76,.76])M(un,d,.8,0,.2,.75,1.45,u.oak),M(un,d,1.2,.02,.24,.11,1.51,u.edge);for(let d=0;d<5;d++)for(let _=0;_<5;_++)ge(un,-.25+d*.115,.92+_*.115,-.4,.118,(d+_)%3?u.cream:u.mustard);M(un,0,1.86,-.6,1.3,.055,.24,u.rose),M(un,0,1.55,-.72,1.22,.48,.055,y(u.rose,u.cream,.34)),M(un,-.18,.86,-.2,.36,.045,.27,u.paper),M(un,-.18,.905,-.2,.3,.022,.22,u.tealDark);let Gr=xe(B.readingLamp=Q("readingLamp",-4.1,0,.16));Ce(Gr,0,1.13,0,1.08),M(Gr,0,.61,0,.07,1.25,.07,u.edge),M(Gr,0,.065,0,.5,.13,.5,u.brass);let Yi=xe(B.stool=Q("stool",-.95,0,1.6));for(let d=0;d<5;d++)M(Yi,0,.16+d*.095,0,.95-Math.abs(d-2)*.09,.095,.83,d%2?"#c89963":"#d9b486");M(Yi,0,.67,0,1.25,.1,1,u.edge),M(Yi,-.14,.75,.03,.74,.045,.56,u.oak);for(let d of[-.32,.02])M(B.stool,d,.83,.02,.19,.13,.19,u.paper),M(B.stool,d,.9,.02,.13,.015,.13,u.edge);let Vr=B.art=Q("art",3.92,2.99,-4.25);M(Vr,0,0,0,.58,.82,.08,u.edge),M(Vr,0,0,.05,.46,.68,.04,u.paper);for(let d=0;d<6;d++)ge(Vr,-.12+d%3*.09,-.2+d*.075,.087,.09,d%2?u.green:u.mustard);let vr=B.entrance=Q("entrance",5.89,0,1.25,-Math.PI/2),Ri=Q("entrance",0,0,0,0,vr);for(let d of[-1.25,1.25]){for(let _=.15;_<3.6;_+=.3)M(Ri,d,_,0,.3,.294,.24,_<1.5?"#7d9b90":"#eae3d0");M(Ri,d*.87,1.65,.02,.13,3.35,.33,u.cream)}M(Ri,0,3.48,0,2.8,.3,.28,u.cream),M(Ri,0,3.29,.02,2.28,.13,.33,u.oak),M(Ri,0,.025,-.16,2.2,.05,.58,u.edge);for(let d=0;d<3;d++)M(Ri,0,-.13-d*.17,-.54-d*.36,2.48,.17,.4,"#c6cab9");let Pi=Q("entrance",-1.04,0,.03,0,vr),yr=Q("entrance",0,0,0,0,Pi);M(yr,1.025,1.63,0,2.03,3.2,.13,u.tealDark);for(let d of[.17,1.88])M(yr,d,1.63,.08,.095,2.94,.045,u.oak);for(let d of[.25,1.33,3.02])M(yr,1.025,d,.08,1.8,.08,.04,u.oak);for(let d=0;d<8;d++)M(yr,.32+d*.2,2.18,.075,.14,1.47,.025,"#537e73");M(Pi,1.74,1.54,.15,.08,.28,.07,u.brass),M(Pi,1.63,1.61,.23,.3,.055,.07,u.brass),M(vr,0,.06,.63,1.76,.045,.66,u.rose);for(let d=0;d<10;d++)M(vr,-.76+d*.169,.086,.63,.02,.006,.54,u.cream);for(let[d,_]of Object.entries(B))if(P[d]&&(k[d]={},!V[d]))for(let A of["cream","ink"]){let b=Q(d,0,0,0,0,_);b.visible=!1,k[d][A]=b;let I=A==="cream",le=I?u.cream:u.navy;if(d==="bed"){for(let Z=0;Z<(I?3:9);Z++)M(b,I?-.84+Z*.84:-1.2+Z*.3,1.18,-1.73,I?.8:.2,I?1.45:1.65-Math.abs(Z-4)*.1,.23,le);M(b,0,.26,0,2.64,.24,3.58,I?u.paper:u.navy)}else if(d==="lounge"){M(b,0,1.17,-.49,1.23,I?1.12:1.48,.25,le);for(let Z of[-.76,.76])M(b,Z,.93,.05,.25,I?.62:.34,1.48,le)}else if(d==="desk")if(M(b,0,.95,-.47,2.64,.36,.12,le),I)M(b,1.04,1.17,.02,.74,.13,1.07,u.cream);else for(let Z of[-1.43,1.43])M(b,Z,.32,0,.16,.07,1.14,u.brass);else if(d==="shelf"){for(let Z=0;Z<4;Z++)M(b,(Z%2?1:-1)*.27,.59+Z*.84,.05,.075,.72,.72,le);M(b,0,3.64,0,I?2.9:3.12,.13,I?.87:1,le)}else if(d==="wardrobe"){M(b,0,3.51,0,I?2.86:3.06,I?.1:.18,1.2,le);for(let Z of w){let ue=Q(d,0,0,0,0,Z.group);ue.visible=!1,((Cd=b.userData).doorPanels||(Cd.doorPanels=[])).push(ue);for(let ye=0;ye<3;ye++)M(ue,-Z.side*.65,.98+ye*.87,.077,1.08,I?.71:.025,.035,le)}}else if(d==="stool"){M(b,0,.7,0,I?1.38:1.27,.12,I?1.1:1.04,le);for(let Z of[-.5,.5])M(b,Z,.28,0,.09,.53,.78,I?u.cream:u.brass)}else if(d==="radio"){M(b,0,1.04,0,2.5,.12,1.06,le);for(let Z of[-1.06,1.06])M(b,Z,.63,.55,.07,.7,.1,I?u.cream:u.brass)}else if(d==="readingLamp")for(let Z=0;Z<3;Z++)M(b,0,1.95+Z*.1,0,I?.62:.8-Z*.18,.1,I?.62:.8-Z*.18,le);else if(d==="garden")for(let Z=0;Z<3;Z++)M(b,-.62+Z*.55,.1+Z*.13,-.13,I?.42:.08,.18+Z*.26,.56,le);else if(d==="rug")for(let Z=-15;Z<=15;Z++)M(b,Z*.14,.05,0,.06,.014,2.4*Math.sqrt(1-(Z/18)**2),I?u.paper:Z%3?u.navy:u.brass)}function F(d,_,A,b,I){let le=new N(..._),Z=new N(...A),ue=le.distanceTo(Z),ye=Math.ceil(ue/(b*.7));for(let Ue=0;Ue<=ye;Ue++){let ae=le.clone().lerp(Z,Ue/ye);ge(d,ae.x,ae.y,ae.z,b,I)}}function ce(d,_,A,b,I,le,Z){for(let ue=0;ue<=Math.floor(I/.13);ue++)M(d,_-I/2+ue*.13,A,b,.035,le,.038,Z);for(let ue=0;ue<=Math.floor(le/.13);ue++)M(d,_,A-le/2+ue*.13,b+.021,I,.035,.025,Z)}function de(d,_,A,b,I,le,Z,ue,ye=0){for(let Ue=-I;Ue<=I;Ue+=.11)for(let ae=-le;ae<=le;ae+=.11){let Me=Math.hypot(Ue/I,ae/le),pe=ye?.88+.12*Math.cos(Math.atan2(ae/le,Ue/I)*ye):1;Me<=pe&&M(d,_+Ue,A,b+ae,.113,Z,.113,ue)}}function be(d,_){var Ue;let A=B[d],b=h[_],I=Q(d,0,0,0,0,A);I.visible=!1,I.userData.variant=_,k[d][_]=I;let le=_==="rattan",Z=_==="walnut",ue=_==="loft",ye=_==="blossom";if(d==="desk"){for(let ae of[-1.39,1.39])if(ue){for(let Me of[-.46,.46])M(I,ae,.58,Me,.1,1.16,.1,b.edge);M(I,ae,.08,0,.11,.1,1.02,b.edge),F(I,[ae,.14,-.4],[ae,1.13,.4],.07,b.edge)}else if(_==="nordic"){for(let Me of[-.48,.48])F(I,[ae,.08,Me],[ae,1.17,0],.11,b.wood);M(I,ae,.43,0,.09,.09,.69,b.edge)}else for(let Me of[-.43,.43])F(I,[ae,.07,Me],[ae*.95,1.17,Me*.85],ye?.14:.11,b.edge);if(M(I,Z||ye?0:.99,1,.03,Z||ye?2.55:.81,.33,.98,b.wood),le)ce(I,.99,1,.54,.69,.25,b.light);else for(let ae=0;ae<(Z||ye?3:1);ae++){let Me=Z||ye?-.85+ae*.85:.99;M(I,Me,1,.535,Z||ye?.79:.68,.27,.05,b.accent),M(I,Me,1,.58,ye?.09:.2,.045,.05,ye?u.cream:u.brass)}ue&&F(I,[-1.28,.18,-.47],[1.28,1.12,-.47],.065,b.edge)}else if(d==="shelf"){for(let ae of[-1.36,1.36])for(let Me of[-.31,.3])M(I,ae,1.77,Me,ue?.085:.12,3.54,.12,b.edge);if(le)ce(I,0,1.81,-.35,2.59,3.25,b.wood);else if(ue)F(I,[-1.27,.26,-.35],[1.27,3.5,-.35],.06,b.edge),F(I,[1.27,.26,-.35],[-1.27,3.5,-.35],.06,b.edge);else{M(I,0,1.78,-.36,2.63,3.38,.075,Z?b.dark:b.light);for(let ae=0;ae<4;ae++)M(I,(ae%2?1:-1)*.52,.6+ae*.84,.04,.09,.73,.68,b.wood)}if(ye)for(let ae=-6;ae<=6;ae++)M(I,ae*.21,3.5+.22*Math.sqrt(1-(ae/7)**2),0,.205,.18,.79,b.accent);Z&&(M(I,0,3.52,0,2.85,.15,.9,b.wood),M(I,0,.06,0,2.7,.12,.8,b.dark))}else if(d==="wardrobe"){for(let ae of w){let Me=Q(d,0,0,0,0,ae.group),pe=-ae.side*.647;Me.visible=!1,Me.userData.variant=_,((Ue=I.userData).doorPanels||(Ue.doorPanels=[])).push(Me),M(Me,pe,1.84,-.015,1.28,2.92,.08,le?b.dark:b.accent);for(let Re of[-.59,.59])M(Me,pe+Re,1.84,.045,.075,2.92,.07,b.wood);for(let Re of[.43,3.25])M(Me,pe,Re,.045,1.22,.08,.07,b.wood);if(le)ce(Me,pe,1.84,.052,1.1,2.66,b.light);else if(Z)for(let Re=0;Re<8;Re++)M(Me,pe-.49+Re*.14,1.84,.06,.06,2.6,.04,Re%2?b.wood:b.dark);else if(ue){for(let Re of[-.53,.53])for(let ct of[.62,1.4,2.22,3.06])ge(Me,pe+Re,ct,.087,.06,u.brass);for(let Re=0;Re<6;Re++)M(Me,pe,2.36+Re*.11,.042,.72,.035,.026,b.dark)}else if(ye){for(let Re=-4;Re<=4;Re++)M(Me,pe+Re*.11,2.01+.12*Math.sqrt(1-(Re/5)**2),.05,.105,1.84+.24*Math.sqrt(1-(Re/5)**2),.04,b.light);for(let Re=0;Re<4;Re++)ge(Me,pe+Math.cos(Re*Math.PI/2)*.1,2.66+Math.sin(Re*Math.PI/2)*.1,.09,.12,u.cream)}else M(Me,pe-ae.side*.23,1.84,.045,.53,2.74,.04,b.light),M(Me,pe,.97,.075,1.12,.06,.03,b.wood)}(Z||ye)&&M(I,0,3.49,0,2.92,.12,1.16,b.wood)}else if(d==="bed"){if(le){M(I,0,1.22,-1.87,2.65,1.68,.08,b.dark),ce(I,0,1.26,-1.8,2.42,1.38,b.light);for(let ae of[-1.3,1.3])M(I,ae,1.14,-1.83,.11,1.94,.15,b.wood);for(let ae of[.53,2.03])M(I,0,ae,-1.8,2.7,.1,.13,b.wood)}else if(ue){for(let ae of[-1.27,1.27])M(I,ae,1.05,-1.86,.1,1.91,.1,b.edge);for(let ae of[.8,1.98])M(I,0,ae,-1.86,2.58,.08,.1,b.edge);for(let ae=-5;ae<=5;ae++)M(I,ae*.22,1.4,-1.86,.055,1.12,.055,b.edge)}else for(let ae=-7;ae<=7;ae++){let Me=ye?1.22+.6*Math.sqrt(1-(ae/8)**2):Z?1.25+.35*Math.abs(ae/7):1.12+.5*(1-Math.abs(ae/7));M(I,ae*.175,.43+Me/2,-1.84,.17,Me,ye?.26:.16,ye?ae%3?b.accent:b.light:Z?b.wood:ae<0?b.accent:b.light)}if(Z)for(let ae of[-1.35,1.35])M(I,ae,1.18,-1.68,.12,1.5,.4,b.wood)}else if(d==="radio"){M(I,0,.98,0,2.36,.12,.98,b.wood),M(I,0,.37,0,2.2,.1,.88,b.wood);for(let ae of[-1.06,1.06]){M(I,ae,.68,0,.11,.56,.9,b.wood);for(let Me of[-.32,.32])F(I,[ae,.03,Me],[ae*.94,.38,Me*.9],.095,b.edge)}for(let ae of[-.54,.54])if(le)M(I,ae,.67,.39,.98,.47,.045,b.dark),ce(I,ae,.67,.43,.93,.43,b.light);else{if(M(I,ae,.67,.43,.97,.47,.05,Z?b.wood:ae<0?b.accent:b.light),M(I,ae+.32,.7,.49,.09,.055,.04,u.brass),ue)for(let Me of[-.4,.4])for(let pe of[.5,.84])ge(I,ae+Me,pe,.48,.05,b.edge);if(Z)for(let Me=0;Me<7;Me++)M(I,ae-.41+Me*.137,.67,.47,.025,.42,.025,b.light)}}else if(d==="garden"){for(let ae=0;ae<3;ae++){let Me=-.62+ae*.55,pe=.18+ae*.26;ye?de(I,Me,pe,-.13,.4,.43,.08,ae%2?b.light:b.accent,5):M(I,Me,pe,-.13,.66,.08,.79,b.wood);for(let Re of[-.43,.16])_==="nordic"&&F(I,[Me-.24,.04,Re],[Me,pe,Re],.065,b.edge),M(I,Me+(_==="nordic"?.24:0),pe/2,Re,ue?.06:.09,pe,.07,b.edge)}(le||ue)&&(F(I,[-.88,.1,-.48],[.78,.94,-.48],.065,b.edge),F(I,[-.88,.08,-.48],[-.88,.6,-.48],.07,b.edge))}else if(d==="lounge"){for(let ae of[-.55,.55])for(let Me of[-.49,.49])F(I,[ae,.05,Me],[ae*.9,.63,Me*.83],ue?.075:.1,b.edge);if(M(I,0,.56,0,1.36,.18,1.35,b.wood),M(I,0,.71,.02,1.25,.19,1.19,b.fabric),le){ce(I,0,1.27,-.59,1.25,1.02,b.light);for(let ae of[-.66,.66])M(I,ae,1.26,-.6,.09,1.2,.11,b.wood);M(I,0,1.85,-.6,1.42,.08,.13,b.wood)}else if(ye||Z){for(let ae=-4;ae<=4;ae++){let Me=.83+.45*Math.sqrt(1-(ae/5)**2);M(I,ae*.15,.65+Me/2,-.55,.145,Me,.22,ye&&ae%2?b.light:b.accent)}for(let ae of[-.7,.7])M(I,ae,1.32,-.38,.16,.88,.48,b.accent)}else{M(I,0,1.24,-.58,1.23,.98,.16,b.accent);for(let ae of[-.68,.68])F(I,[ae,.12,.6],[ae,1.8,-.6],ue?.08:.12,b.edge)}for(let ae of[-.75,.75])M(I,ae,.98,.1,.13,.47,.12,b.edge),M(I,ae,1.19,0,.2,.1,1.43,Z?b.wood:b.light);M(I,.12,1.01,-.31,.57,.49,.18,ye?u.cream:b.light,0,-.13)}else if(d==="readingLamp"){if(_==="nordic")for(let ae=0;ae<3;ae++){let Me=ae*Math.PI*2/3;F(I,[Math.cos(Me)*.36,.06,Math.sin(Me)*.36],[0,1.78,0],.07,b.wood)}else de(I,0,.07,0,.3,.3,.12,b.edge),M(I,0,.91,0,.07,1.76,.07,b.edge);if(le){for(let ae of[1.7,2.31])M(I,0,ae,0,.64,.08,.64,b.wood);for(let ae of[-.29,.29])for(let Me=-.27;Me<.3;Me+=.09)M(I,ae,2,Me,.045,.59,.045,b.wood);for(let ae of[-.29,.29])for(let Me=-.27;Me<.3;Me+=.09)M(I,Me,2,ae,.045,.59,.045,b.wood);M(I,0,2,0,.42,.49,.42,"#ffdfa0")}else if(ue){F(I,[0,1.7,0],[.35,2.16,0],.08,b.edge),ge(I,0,1.7,0,.15,u.brass);for(let ae=0;ae<4;ae++)M(I,.29,1.98+ae*.09,0,.56-ae*.1,.095,.47-ae*.075,b.accent)}else for(let ae=0;ae<7;ae++){let Me=Z?.44*Math.sqrt(1-(ae/8)**2):ye?.16+Math.sin(ae/6*Math.PI)*.19:.43-ae*.039;de(I,0,1.78+ae*.077,0,Me,Me,.079,ae===0?"#ffe2a6":ye?ae%2?b.light:b.accent:Z?"#c58d49":b.light,ye?5:0)}}else if(d==="stool")if(le){for(let ae of[-.39,.39])ce(I,0,.35,ae,.91,.48,b.light);for(let ae of[-.47,.47])M(I,ae,.34,0,.07,.55,.8,b.wood);M(I,0,.69,0,1.25,.12,1.02,b.wood)}else{if(ue){M(I,0,.69,0,1.29,.12,1.02,b.wood);for(let ae of[-.52,.52]){for(let Me of[-.39,.39])M(I,ae,.33,Me,.07,.59,.07,b.edge);M(I,ae,.08,0,.075,.075,.86,b.edge)}}else{de(I,0,.69,0,Z?.76:.7,.56,.11,ye?b.light:b.wood,ye?5:0);for(let ae=0;ae<3;ae++){let Me=ae*Math.PI*2/3;F(I,[Math.cos(Me)*.49,.04,Math.sin(Me)*.4],[Math.cos(Me)*.31,.66,Math.sin(Me)*.3],.095,b.edge)}}Z&&de(I,0,.23,0,.61,.43,.07,b.wood)}else if(d==="rug"){for(let ae=-22;ae<=22;ae++)for(let Me=-16;Me<=16;Me++){let pe=ae/22,Re=Me/16,ct=Math.hypot(pe,Re);if(!(ye?ct<=.87+.13*Math.cos(Math.atan2(Re,pe)*6):ue?!0:ct<=1))continue;let Nt=le?Me%3?b.light:b.wood:Z?(Math.abs(ae)+Math.abs(Me))%9<3?b.accent:b.light:ue?(Math.floor((ae+22)/6)+Math.floor((Me+16)/6))%2?b.dark:b.fabric:ye?ct<.24?u.mustard:ct>.68?b.accent:b.light:Me+16<Math.abs(ae)*.8?b.light:Me%9<3?b.dark:b.fabric;M(I,ae*.113,.027,Me*.113,.111,.04,.111,Nt)}if(le)for(let ae=-11;ae<=11;ae++)for(let Me of[-1,1])M(I,ae*.14,.023,Me*(1.76-Math.abs(ae)*.038),.045,.028,.24,b.light)}}function _e(d){let _=f[d],A=Q("");A.visible=!1,K[d]=A;let b=Q("",0,0,0,0,Je),I=Q("entrance",0,0,0,0,Pi);b.visible=!1,I.visible=!1,A.userData.parts=[b,I];let le=d==="washitsu",Z=d==="loft",ue=d==="french",ye=d==="coastal",Ue=d==="cabin",ae=Ue?.28:Z?.23:.3,Me=Z?.59:Ue?1.47:.6;for(let pe=0,Re=ae/2;Re<4.8;pe++,Re+=ae){for(let ct=-5.98+(Z&&pe%2?-Me/2:0);ct<5.99;ct+=Me){let Jt=Math.max(-5.98,ct),Nt=Math.min(5.99,ct+Me),ei=Re-ae/2,om=Re+ae/2>1.6&&ei<4.29?[[Jt,Math.min(Nt,-5.25)],[Math.max(Jt,-1.93),Nt]]:[[Jt,Nt]];for(let[Gc,Vc]of om)if(Vc-Gc>.018){let lm=Z?[_.wall,_.lower,"#c08b70","#aa725e"][Math.abs(pe+Math.round(ct*10+60))%4]:Ue?[_.wall,_.lower,"#bc956e"][(pe+60+Math.round(ct))%3]:Re<1.48?_.lower:_.wall;M(A,(Gc+Vc)/2,Re,-4.47,Vc-Gc-.012,ae-.012,Ue?.27:.22,lm)}}for(let ct=-4.5+(Z&&pe%2?-Me/2:0);ct<5.7;ct+=Me){let Jt=Math.max(-4.5,ct),Nt=Math.min(5.7,ct+Me);if(Re>1.48&&(Jt+Nt)/2>1.7)continue;let ei=Z?[_.wall,_.lower,"#c08b70"][(pe+Math.round(ct*10+60))%3]:Re<1.48?_.lower:_.wall;M(A,-5.9,Re,(Jt+Nt)/2,Ue?.27:.22,ae-.012,Nt-Jt-.012,ei)}}for(let pe of[.13,4.8])M(A,0,pe,-4.29,12,Ue?.2:.09,Ue?.3:.14,_.edge);if(M(A,-5.72,.13,.6,.14,.09,10.2,_.edge),M(A,-5.86,4.8,-1.35,Ue?.35:.2,Ue?.23:.1,6.1,_.edge),le||Ue||Z){for(let pe of[-5.77,-1.73,5.79])M(A,pe,2.43,-4.25,Ue?.22:.13,4.8,Ue?.28:.14,_.edge);for(let pe of[-4.36,1.62])M(A,-5.7,2.41,pe,.16,4.7,Ue?.22:.14,_.edge)}if(Z){for(let pe of[-5.78,-1.73,5.78])for(let Re=.35;Re<4.65;Re+=.52)ge(A,pe,Re,-4.16,.065,"#a1a69c");for(let pe of[4.44,4.65])M(A,0,pe,-4.29,11.84,.055,.18,_.edge)}if(ue){for(let pe of[.25,1.48,4.54,4.7])M(A,0,pe,-4.28,11.85,.07,.08,"#fff4df"),M(A,-5.71,Math.min(pe,1.48),.6,.08,.07,10.06,"#fff4df");for(let pe=-5.23;pe<5.6;pe+=1.32){for(let Re of[-.5,.5])M(A,pe+Re,.86,-4.23,.032,.97,.026,"#fff3db");for(let Re of[.37,1.34])M(A,pe,Re,-4.23,1.03,.03,.026,"#fff3db")}for(let pe of[2.35,4.68]){for(let Re of[-.96,.96])M(A,pe+Re,3.07,-4.26,.04,2.17,.05,"#fff5e3");for(let Re of[1.97,4.17])M(A,pe,Re,-4.26,1.96,.04,.05,"#fff5e3")}}if(ye){M(A,0,1.5,-4.26,11.85,.075,.12,_.wood),M(A,-5.7,1.5,.6,.12,.075,10.09,_.wood);for(let pe=-5.7;pe<5.9;pe+=.36)M(A,pe,.78,-4.27,.026,1.3,.03,"#bccdc2")}if(le)for(let pe=0;pe<7;pe++)for(let Re=0;Re<4;Re++){let ct=-4.47+Re*2.98,Jt=-3.75+pe*1.455;M(A,ct,-.038,Jt,2.96,.065,1.435,_.edge),M(A,ct,-.006,Jt,2.81,.012,1.285,_.floor[(pe+Re)%3]);for(let Nt=0;Nt<11;Nt++)M(A,ct,.001,Jt-.55+Nt*.11,2.76,.004,.018,"#a9a578")}else if(Z||ye){let pe=Z?1.49:.596;for(let Re=0;Re<(Z?7:17);Re++)for(let ct=0;ct<(Z?8:20);ct++){let Jt=-5.96+pe/2+ct*pe,Nt=-4.47+pe/2+Re*pe,ei=ye&&(Re===0||Re===16||ct===0||ct===19),Hc=Math.min(pe,5.68-(Nt-pe/2));M(A,Jt,-.032,Nt-(pe-Hc)/2,pe-.017,.05,Hc-.017,ei?_.edge:_.floor[(Re*2+ct)%3]),ei&&M(A,Jt,-.004,Nt,.2,.006,.2,"#eee9d5",Math.PI/4)}}else for(let pe=0;pe<23;pe++)for(let Re=0;Re<8;Re++){let ct=-5.96+Re*1.49,Jt=-4.46+pe*.442;if(ue){let Nt=(pe+Re)%2===0;for(let ei=0;ei<3;ei++)M(A,ct+.745+(Nt?(ei-1)*.49:0),-.032,Jt+.223+(Nt?0:(ei-1)*.145),Nt?.48:1.47,.05,Nt?.432:.139,_.floor[ei])}else{M(A,ct+.745,-.032,Jt+.223,1.474,.05,.432,_.floor[(pe+Re)%3]);for(let Nt of[-.12,.12])M(A,ct+.745,-.005,Jt+.223+Nt,1.2,.004,.009,_.wood)}}for(let pe of[-1.65,1.65])M(b,pe,0,.1,Ue?.2:.12,2.9,.28,_.wood);for(let pe of[-1.4,1.4])M(b,0,pe,.1,3.5,Ue?.18:.12,.28,_.wood);if(M(b,0,-1.48,.24,3.7,.14,.68,_.wood),le)M(b,0,0,.014,3.19,2.67,.03,"#eee9cb"),ce(b,0,0,.11,3.15,2.62,_.wood);else if(ue){for(let pe of[-.52,.52])M(b,pe,-.22,.15,.055,2.25,.08,"#fff5df");for(let pe=-14;pe<=14;pe++){let Re=pe/14,ct=.62+.72*Math.sqrt(Math.max(0,1-Re*Re));M(b,Re*1.53,ct,.17,.117,.12,.12,"#fff6e2"),ct<1.28&&M(b,Re*1.53,(ct+1.36)/2,.06,.114,1.36-ct,.1,_.wall)}M(b,0,-.24,.15,3.16,.055,.08,"#fff5df")}else{for(let pe=1;pe<(Z?5:3);pe++)M(b,-1.65+pe*3.3/(Z?5:3),0,.14,Z?.05:.07,2.7,.09,_.edge);for(let pe of Z?[-.68,0,.68]:[0])M(b,0,pe,.14,3.24,.06,.09,_.edge)}if(ye)for(let pe of[-1,1]){M(b,pe*1.85,0,.25,.38,2.88,.11,_.edge);for(let Re=0;Re<19;Re++)M(b,pe*1.85,-1.29+Re*.143,.33,.34,.065,.1,_.accent)}if(Ue){M(b,0,1.52,.05,3.96,.21,.45,_.edge);for(let pe of[-1.74,1.74])for(let Re of[-1.32,1.32])ge(b,pe,Re,.27,.09,"#343f37")}le||Ke(b,.34,ue?"#fff6e6":ye?"#f3f0dd":Ue?"#d8c29f":Z?"#d8cbb7":_.wall,ye?_.wood:_.accent,_.wood),M(I,1.025,1.63,0,2.03,3.2,.13,le?"#e2ddbe":Z?_.edge:ue?_.wall:_.wood);for(let pe of[.08,1.97])M(I,pe,1.63,.08,.09,3.12,.06,_.edge);for(let pe of[.11,3.16])M(I,1.025,pe,.08,1.95,.09,.06,_.edge);if(le)ce(I,1.025,1.67,.087,1.77,2.83,_.wood);else if(ue)for(let[pe,Re]of[[.76,.96],[2.19,1.36]]){for(let ct of[.27,1.77])M(I,ct,pe,.09,.045,Re,.04,_.wood);for(let ct of[pe-Re/2,pe+Re/2])M(I,1.02,ct,.09,1.55,.045,.04,_.wood)}else if(Z){for(let pe of[.21,1.84])for(let Re=.32;Re<3.1;Re+=.43)ge(I,pe,Re,.09,.075,"#a9b3a7");for(let pe=0;pe<5;pe++)M(I,1.025,2.28+pe*.13,.075,1.24,.038,.025,_.accent)}else{for(let pe=0;pe<8;pe++)M(I,.24+pe*.223,1.63,.079,.026,2.94,.028,_.edge);if(Ue){for(let pe of[.58,2.73])M(I,1.025,pe,.12,1.8,.13,.08,_.edge);F(I,[.27,.68,.12],[1.8,2.63,.12],.1,_.edge)}}He()}function Fe(d,_){let A=B[d],b=Q(d,0,0,0,0,A);(T[d]||(T[d]={}))[_]=b;let I=(ue=1.15)=>{M(b,0,.76,0,ue,.12,.8,u.oak);for(let ye of[-1,1])for(let Ue of[-1,1])M(b,ye*(ue/2-.12),.37,Ue*.28,.12,.74,.12,u.edge)},le=(ue,ye,Ue=1.45,ae=.9)=>{M(b,0,ue,ye,Ue+.16,ae+.16,.16,u.dark),M(b,0,ue,ye+.09,Ue,ae,.035,u.tealDark);for(let Me=0;Me<8;Me++)ge(b,-.52+Me*.14,ue-.25+Me%3*.15,ye+.115,.1,Me%2?u.mustard:u.tealLight)},Z=(ue,ye)=>{M(b,0,ue,0,.8,ye?.94:.19,ye?.22:.57,u.teal);for(let Ue=0;Ue<10;Ue++){let ae=Ue*Math.PI/5;ge(b,Math.cos(ae)*.16,ye?ue-.07+Math.sin(ae)*.16:ue+.13,ye?.14:Math.sin(ae)*.16,.065,u.brass)}M(b,0,ue+(ye?.3:.23),ye?.23:-.1,.84,.13,.15,u.rose);for(let Ue of[-.34,.34])M(b,Ue,ue+(ye?.25:.19),ye?.25:-.1,.2,.23,.24,u.rose);for(let Ue=0;Ue<16;Ue++)ge(b,.44+Ue%2*.06,ue-.04-Ue*.035,.1,.06,u.dark)};if(_==="chess"){I(1.55),M(b,0,.87,0,1.5,.12,1.3,u.edge);for(let ue=0;ue<8;ue++)for(let ye=0;ye<8;ye++)M(b,-.56+ue*.16,.94,-.56+ye*.16,.155,.025,.155,(ue+ye)%2?u.cream:u.tealDark);for(let ue=0;ue<8;ue++)for(let ye of[-.56,.56])M(b,-.56+ue*.16,1.01,ye,.09,.12,.09,ye<0?u.cream:u.rose),ge(b,-.56+ue*.16,1.1+ue%3*.015,ye,.08,ye<0?u.cream:u.rose)}else if(_==="arcade"){M(b,0,.64,0,1.14,1.28,.95,u.teal),M(b,0,1.43,-.26,1.14,1.62,.44,u.teal),le(1.62,.005,.88,.66),M(b,0,2.25,-.06,1.2,.27,.8,u.rose),M(b,0,1.22,.31,1.2,.13,.62,u.dark),M(b,-.3,1.38,.37,.055,.22,.055,u.edge),ge(b,-.3,1.5,.37,.13,u.rose);for(let ue=0;ue<3;ue++)ge(b,.13+ue*.17,1.31,.39,.1,ue%2?u.mustard:u.tealLight);for(let ue=0;ue<6;ue++)ge(b,-.4+ue*.16,2.25,.35,.08,u.cream)}else if(_==="console"){le(.1,.03),M(b,0,-.63,.16,1.62,.1,.45,u.oak),M(b,0,-.51,.19,.7,.15,.28,u.cream);for(let ue of[-.22,.22])ge(b,ue,-.42,.3,.1,u.rose)}else if(_==="camera"||_==="instant"){let ue=_==="camera"?1.65:1.08;if(_==="instant")I();else{M(b,0,.95,0,.07,1.18,.07,u.edge);for(let[ye,Ue]of[[-.5,.32],[.5,.32],[0,-.5]])F(b,[0,1.25,0],[ye,.07,Ue],.065,u.edge)}M(b,0,ue,0,.88,.57,.37,_==="instant"?u.cream:u.teal),M(b,-.06,ue,.26,.37,.37,.23,u.dark),M(b,-.06,ue,.39,.23,.23,.045,u.tealLight),M(b,-.21,ue+.33,-.02,.28,.11,.2,u.dark),ge(b,.28,ue+.3,0,.12,u.rose),_==="instant"&&(M(b,0,ue-.36,.23,.6,.025,.45,u.paper),M(b,0,ue-.34,.23,.45,.012,.29,u.tealLight))}else if(_==="gallery"){M(b,0,.48,0,1.75,.065,.1,u.edge);for(let ue=-1;ue<=1;ue++)M(b,ue*.57,.07-ue%2*.1,.04,.49,.67,.07,u.cream),M(b,ue*.57,.12-ue%2*.1,.085,.37,.43,.02,ue===0?u.tealLight:u.rose),ge(b,ue*.57,.44,.08,.09,u.brass)}else _==="telephone"?(I(),Z(1.02,!1)):_==="wallPhone"?(Z(0,!0),M(b,0,0,-.15,1.08,1.44,.1,u.oak)):_==="monitor"?(I(.9),M(b,0,1.08,0,.14,.35,.15,u.navy),M(b,0,1.48,0,1.13,.73,.12,u.navy),M(b,0,1.48,.071,.97,.57,.025,u.teal),M(b,-.18,1.56,.09,.47,.04,.025,u.paper),M(b,-.25,1.44,.09,.33,.04,.025,u.tealLight),M(b,0,.955,.32,.63,.04,.2,u.cream)):_==="pedestal"&&(I(.8),M(b,0,.99,0,.52,.34,.44,u.teal),M(b,0,1.18,0,.58,.045,.5,u.paper),ge(b,0,1.28,0,.16,u.brass));He()}for(let[d,_,A]of[["game",.6,3.5],["image",2.9,4.65],["together",-2.2,4.5]])B[d]=Q(d,_,0,A),k[d]={},Fe(d,Object.keys(V[d])[0]);function He(){for(let[d,_]of D){let A=new Tn(re,Se,_.length);_.forEach((b,I)=>{C.set(b.x,b.y,b.z),z.set(b.w,b.h,b.d),L.setFromEuler(new Wn(0,b.ry,b.rz)),G.compose(C,L,z),A.setMatrixAt(I,G),A.setColorAt(I,O.set(b.tint))}),A.castShadow=!0,A.receiveShadow=!0,A.userData.station=d.userData.station,A.userData.baseColors=_.map(b=>b.tint),A.userData.variant=d.userData.variant||"",d.add(A),$.push(A)}D.clear(),S.dataset.voxelCount=String(W)}He();let Xe=new Wa("#e9f2ef","#6d5c46",1.95),Ve=new Lr("#ffe0b0",5);Ve.position.set(-2.5,7.5,8.2),Ve.castShadow=!0,Ve.shadow.mapSize.set(2048,2048),Object.assign(Ve.shadow.camera,{left:-16,right:16,top:17,bottom:-16,near:.5,far:48}),Ve.shadow.normalBias=.02,Ve.shadow.bias=-15e-5,Ve.shadow.radius=2.2;let it=new Lr("#cfe3e7",.55);it.position.set(7,5,-1);let dt=new Lr("#c9e0e2",.32);dt.position.set(-8,6,-6);let _t=new Ps("#ffd094",10,7,2);_t.position.set(-1.98,1.7,-1.09),Pt.add(_t);let et=new Ps("#ffcd83",7,5,2);et.position.set(-1.13,2,-.24),Oe.add(et),ne.add(Xe,Ve,it,dt);let ft=new Et(new vn(180,180),new hn({color:"#d8dfd2",roughness:1}));ft.rotation.x=-Math.PI/2,ft.position.y=-.74,ft.receiveShadow=!0,ne.add(ft);let Dt=document.createElement("canvas");Dt.width=Dt.height=64;let Ut=Dt.getContext("2d"),wt=Ut.createLinearGradient(0,0,0,64);wt.addColorStop(0,"rgba(0,0,0,.34)"),wt.addColorStop(1,"rgba(0,0,0,0)"),Ut.fillStyle=wt,Ut.fillRect(0,0,64,64);let mt=new ri(Dt),At=document.createElement("canvas");At.width=At.height=64;let Ht=At.getContext("2d"),at=Ht.createLinearGradient(0,0,64,0);at.addColorStop(0,"rgba(0,0,0,.34)"),at.addColorStop(1,"rgba(0,0,0,0)"),Ht.fillStyle=at,Ht.fillRect(0,0,64,64);let lt=new ri(At),xn=new vn(1,1),xr=new cn({map:mt,color:"#2b2118",transparent:!0,opacity:.62,depthWrite:!1,toneMapped:!1}),Zi=new cn({map:lt,color:"#2b2118",transparent:!0,opacity:.62,depthWrite:!1,toneMapped:!1}),ai=new Et(xn,xr);ai.rotation.x=-Math.PI/2,ai.position.set(0,.012,-3.86),ai.scale.set(11.86,1.05,1),ai.renderOrder=1;let dn=new Et(xn,Zi);dn.rotation.x=-Math.PI/2,dn.position.set(-5.29,.012,.62),dn.scale.set(1.05,10.02,1),dn.renderOrder=1,ne.add(ai,dn);let Un=kf(ne);S.dataset.environment="courtyard";let zt=new Xa(new ln,"#dfb55d");zt.material.depthTest=!0,zt.material.transparent=!0,zt.material.opacity=.32,zt.visible=!1,zt.renderOrder=8,ne.add(zt);let Nn=document.createElement("div");Nn.className="home-room-hover-title",Nn.hidden=!0,Nn.setAttribute("role","tooltip"),S.appendChild(Nn);let pn=new ja(12,48,"#547d71","#8ba698");pn.position.set(0,.04,.6),pn.scale.z=.85,pn.visible=!1,pn.material.transparent=!0,pn.material.opacity=.3,ne.add(pn);let An=!1,oi=!1,Mr=0,Mn="overview",Ze="",vt=!0,Ot=!1,On=-1,xt=null,Ii=0,Jn=!1,Tt=1,Cn=1,Li=0,qt=!1,Yt=0,ot=!1,Wt="lounge",mi=[],Di=[],bn=null,li="classic",Ct=null,gi={},Ui={},Fn={},_i={},Ni={},Wr={},Kn="",Oi=!1,qs=[],Tc=!!E.reducedMotion,Rn=Tc||!!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches,Ec=window.matchMedia?.("(prefers-reduced-motion: reduce)"),wc=new Ls,Ac=new Ls,id=new Te,ao={x:0,y:0},fn=null,jr=null,Ys={},Zs={photo:null,work:null},Cc=0,ci=zf(ne,()=>({bounds:a,obstacles:[{id:"door-sweep",x:4.8,z:1.365,halfX:1.18,halfZ:1.285,angle:0},...Object.keys(P).filter(d=>d!=="rug"&&B[d]?.visible&&Fn[d]==="floor").map(d=>{let _=Uc(d);return{id:d,x:_.center.x,z:_.center.z,halfX:_.half.x,halfZ:_.half.z,angle:B[d].rotation.y}})],desk:B.desk.visible?{approach:B.desk.localToWorld(new N(-.4,0,2.28)),seat:B.desk.localToWorld(new N(-.4,.795,1.27)),angle:B.desk.rotation.y+Math.PI-.18}:null}),d=>E.onResidentChange?.(d)),Rc=0,Hf=new sn(new N(0,1,0),0);for(let d of Object.keys(P)){let _=B[d];_.updateWorldMatrix(!0,!0);let A=_.matrixWorld.clone().invert(),b=new ln;_.traverse(I=>{I.isInstancedMesh&&(I.computeBoundingBox(),b.union(I.boundingBox.clone().applyMatrix4(A.clone().multiply(I.matrixWorld))))}),Ni[d]=b,Wr[d]={legacy:b.clone()},Ui[d]=V[d]?Object.keys(V[d])[0]:"default",Fn[d]=d==="calendar"?"left-wall":d==="art"?"back-wall":"floor",_i[d]={x:_.position.x,y:_.position.y,z:_.position.z,rotation:_.rotation.y*180/Math.PI,style:"oak",model:Ui[d],surface:Fn[d]},gi[d]="oak"}for(let d of Object.keys(P)){if(d==="rug"||Fn[d]!=="floor")continue;let _=Ni[d];if(!_||_.isEmpty())continue;let A=_.getCenter(new N),b=_.getSize(new N),I=new Et(Ee,ve);I.rotation.x=-Math.PI/2,I.position.set(A.x,.006,A.z),I.scale.set(Math.max(.32,b.x*1.14),Math.max(.32,b.z*1.08),1),I.renderOrder=2,B[d].add(I)}let rd=[];function $n(d,_,A,b,I=1){let le=new cn({color:b,transparent:I<1,opacity:I,depthWrite:I===1});rd.push(le);let Z=new Et(re,le);return Z.position.set(..._),Z.scale.set(...A),d.add(Z),Z}let sd=Q("",-.32,.94,.02,0,B.stool),ad=Array.from({length:7},(d,_)=>$n(sd,[0,_*.1,0],[.08,.07,.07],"#f9eee0",.35)),Js=Q("",0,1.8,0,0,B.garden);$n(Js,[0,0,0],[.07,.2,.07],"#6b533c");let Gf=[-1,1].map(d=>{let _=Q("",d*.04,0,0,0,Js);return $n(_,[d*.11,.045,0],[.22,.21,.045],"#e3b15e"),$n(_,[d*.08,-.09,0],[.15,.11,.045],"#e6d5a3"),$n(_,[d*.16,.08,.025],[.06,.07,.025],"#825f4e"),_}),Ji=Q("",.86,-1.23,.41,0,Je);$n(Ji,[0,0,0],[.3,.25,.23],"#8c9e9a"),$n(Ji,[.14,.13,0],[.19,.2,.2],"#cabca0"),$n(Ji,[.24,.11,0],[.12,.065,.1],"#cb9a4c"),$n(Ji,[.19,.17,.105],[.035,.035,.02],"#344a47"),$n(Ji,[-.18,-.01,0],[.18,.08,.14],"#607d78");for(let d of[-.07,.07])$n(Ji,[d,-.15,.01],[.045,.1,.05],"#b08b52");let od=Q("",0,1.4,0,0,B.garden),Vf=Array.from({length:8},(d,_)=>$n(od,[Math.sin(_*2.4)*.6,_%3*.24,Math.cos(_*2.4)*.6],[.06,.06,.06],"#e6f4ae")),oo={steam:sd,butterfly:Js,bird:Ji,fireflies:od};Object.values(oo).forEach(d=>{d.visible=!1});let Wf={steam:"杯口升起一缕热气，慢慢散在小屋里。",butterfly:"一只小蝴蝶绕着绿植，轻轻停了一会儿。",bird:"窗沿落下一只小鸟，歪着脑袋看了看。",fireflies:"叶片间亮起几粒萤火，像藏起来的小星星。"},Bn="day",Pn="unknown",Xr=!0,qr=!0,lo=0,Pc=0,Yr=0,co=0,Zt="",Ic="",Ks=40+Math.random()*35;function Lc(){return!oi||ot||Ot}function jf(){return{condition:Pn,light:Bn,motion:Xr&&!Rn,events:qr,paused:Lc(),weatherPaused:!oi||ot||!Xr||Rn,event:Zt||null,eventElapsed:Yr,nextIn:Ks,elapsed:lo,precipitation:Un.getState().particles>0,courtyard:Un.getState(),fog:{near:ne.fog?.near,far:ne.fog?.far}}}function $s(){Object.values(oo).forEach(d=>{d.visible=!1}),Zt="",Yr=0,co=0,Ks=90+Math.random()*150,vt=!0,E.onMoment?.(null)}function ld(d=!1){if(An||Lc()||Zt||d&&!qr)return!1;let _=!["rain","snow","storm","mist"].includes(Pn),A=["steam"];Bn==="night"&&_&&A.push("fireflies"),Bn!=="night"&&_&&A.push("bird"),Bn!=="night"&&["clear","cloudy"].includes(Pn)&&A.push("butterfly");let b=A.filter(I=>I!==Ic);return Zt=(b.length?b:A)[Math.floor(Math.random()*(b.length||A.length))],Ic=Zt,Yr=0,co=12,oo[Zt].visible=!0,ho(0),vt=!0,kt(),E.onMoment?.({id:Zt,caption:Wf[Zt]}),!0}function ho(d){Zt==="steam"&&ad.forEach((_,A)=>{let b=(A/ad.length+d*.2)%1;_.position.set(Math.sin(b*7+A)*.08,b*.72,Math.cos(b*6)*.025),_.material.opacity=Math.sin(b*Math.PI)*.46}),Zt==="butterfly"&&(Js.position.set(Math.sin(d*.7)*.52,1.85+Math.sin(d*.9)*.18,Math.cos(d*.7)*.42),Js.rotation.y=-d*.7,Gf.forEach((_,A)=>{_.rotation.y=(A?1:-1)*(.4+Math.sin(d*9)*.55)})),Zt==="bird"&&(Ji.rotation.y=Math.sin(d*.6)*.25),Zt==="fireflies"&&Vf.forEach((_,A)=>{_.position.set(Math.sin(A*2.4+d*.3)*.65,A%3*.24+Math.sin(d*.6+A)*.1,Math.cos(A*2.4+d*.3)*.6)})}function uo(d){let _=Lc();for(let[b,I]of Object.entries(oo)){let le=b===Zt&&!_;I.visible!==le&&(I.visible=le,vt=!0)}if(!oi||ot)return;let A=Math.min(d,.1);Zt&&!_?(co-=A,co<=0&&$s()):!_&&qr&&!fn&&!xt&&(Ks-=A,Ks<=0&&ld(!0)),!(!Xr||Rn)&&(lo+=A,Pc+=A,Zt&&!_&&(Yr+=A),!(Pc<1/24)&&(Pc=0,Un.tick(lo)&&(vt=!0),Zt&&!_&&(ho(Yr),vt=!0)))}function Xf(d={}){let _=["clear","cloudy","rain","snow","storm","mist"].includes(d.condition)?d.condition:"unknown",A=Xr!==(d.motion!==!1),b=qr!==(d.events!==!1);Xr=d.motion!==!1,qr=d.events!==!1,_!==Pn&&(Pn=_,Zt&&$s(),$r(Bn,!0)),b&&!qr&&Zt&&$s(),A&&(Xr||ho(0),vt=!0,kt()),E.onAtmosphereChange?.()}function qf(){$s(),Ic="",lo=0,Yr=0,Ks=40+Math.random()*35,Pn="unknown",$r(Bn,!0)}let br=d=>Math.round(d*100)/100;function zn(){return{version:1,houseStyle:Ct?.target||li,items:{...structuredClone(H),...Object.fromEntries(Object.keys(P).map(d=>[d,{x:br(B[d].position.x),y:br(B[d].position.y),z:br(B[d].position.z),rotation:br(gt.radToDeg(B[d].rotation.y)),style:gi[d],model:Ui[d],surface:Fn[d],...oe.has(d)?{stored:!0}:{}}]))}}}function po(d){return B[d].updateWorldMatrix(!0,!0),Ni[d].clone().applyMatrix4(B[d].matrixWorld)}function Yf(){return Kn||(oe.has(Wt)?"已收纳。位置与外观仍保留，功能可以从清单打开。":Fn[Wt]!=="floor"?"沿墙拖动；方向键左右移动、上下调高低。挂件会避开窗户和家具。":Wt==="rug"?"地毯可铺在家具下。拖动摆放，空白处旋转视角。":"拖动摆放，重叠时保留上一个位置。方向键微调，R 旋转，Ctrl / ⌘ Z 撤销。")}function Sr(){S.dataset.placementBlocked=String(Oi),E.onEditorChange?.({editing:ot,houseStyle:li,id:Wt,label:P[Wt],item:zn().items[Wt],canUndo:mi.length>0,canRedo:Di.length>0,hint:Yf(),blocked:Oi})}function Zr(d){JSON.stringify(d)!==JSON.stringify(zn())&&(mi.push(d),mi.length>60&&mi.shift(),Di=[]),Sr()}function fo(d){if(typeof d!="string")return"classic";let _=v[d]||d;return Object.hasOwn(p,_)?_:"classic"}function Qs(d){let _=fo(d);if(S.dataset.houseStyle=_,Un.setWorld(_),_===li){$r(Bn,!0);return}_!=="classic"&&!K[_]&&_e(_),li=_;for(let I of[Pe,Be,he,je,yr])I.visible=_==="classic";for(let[I,le]of Object.entries(K))le.visible=I===_,le.userData.parts.forEach(Z=>{Z.visible=le.visible});let A=f[_],b=A?{[u.tealDark]:A.edge,[u.edge]:A.edge,[u.oak]:A.wood,[u.cream]:A.wall,"#7d9b90":A.lower,"#eae3d0":A.wall,"#c6cab9":A.floor[1]}:{};for(let I of[fe,Ri])I.traverse(le=>{le.isInstancedMesh&&(le.userData.baseColors.forEach((Z,ue)=>{le.setColorAt(ue,O.set(b[Z]||Z))}),le.instanceColor.needsUpdate=!0)});ne.updateMatrixWorld(!0),Ze="",vt=!0,J.shadowMap.needsUpdate=!0,$r(Bn,!0)}function Dc(d,_){let A=fo(d);if(A===li&&!Ct){_?.();return}if(Rn||!oi){Ct=null,Bi(1),Qs(A),_?.(),kt();return}let b=Ct?U:1;Ct={target:A,phase:"degrade",elapsed:0,degradeDuration:Math.max(.18,.44*b),growDuration:.62,startProgress:b,onCommitted:_},vt=!0}function Zf(d){if(!Ct)return;Ct.elapsed+=Math.min(d,.06);let _=A=>A<.5?2*A*A:1-Math.pow(-2*A+2,2)/2;if(Ct.phase==="degrade"){let A=Math.min(1,Ct.elapsed/Ct.degradeDuration),b=gt.lerp(Ct.startProgress,0,_(A));Bi(b),A>=1&&(Qs(Ct.target),Ct.onCommitted?.(),Ct.onCommitted=null,Ct.phase="grow",Ct.elapsed=0,Bi(0))}else{let A=Math.min(1,Ct.elapsed/Ct.growDuration),b=1-Math.pow(1-A,3);Bi(b),A>=1&&(Ct=null,Bi(1),J.shadowMap.needsUpdate=!0)}vt=!0}function Jf(d){if(!ot)return;let _=zn(),A=fo(d);A===li&&!Ct||Dc(A,()=>Zr(_))}function cd(d,_){if(gi[d]===_)return;let A=!V[d]&&c.includes(_),b=h[_];A&&!k[d][_]&&(be(d,_),He()),gi[d]=_;let I=_==="cream"?{[u.oak]:"#e6ddc6",[u.wood]:"#c8b696",[u.edge]:"#ab987d",[u.teal]:"#d5cdb5",[u.tealDark]:"#879287",[u.tealLight]:"#e8dcc5",[u.rose]:"#cfa796"}:_==="ink"?{[u.oak]:"#78654f",[u.wood]:"#675541",[u.edge]:"#3f464b",[u.teal]:"#3f546c",[u.tealDark]:"#293b50",[u.tealLight]:"#74899b",[u.rose]:"#bc794f"}:b?{[u.oak]:b.wood,[u.wood]:b.wood,[u.edge]:b.edge,[u.teal]:b.accent,[u.tealDark]:b.dark,[u.tealLight]:b.light,[u.rose]:b.rose,"#d8b68b":b.wood,"#c9a779":b.light,"#dc9b79":b.rose}:{};_==="oak"&&X.has(d)&&(I[u.teal]=X.get(d).accent);let le=["#6e9990","#82a89a","#a9c3ac","#c89669","#d4a87a","#bd895d","#e5d8b8","#dbcaab","#6b9890"];B[d].traverse(Z=>{!Z.isInstancedMesh||Z.userData.variant||(Z.userData.baseColors.forEach((ue,ye)=>{let Ue=_!=="oak"&&le.includes(ue)?b?.fabric||(_==="cream"?"#e1d7c4":"#546c85"):ue;Z.setColorAt(ye,O.set(I[ue]||Ue))}),Z.instanceColor.needsUpdate=!0)});for(let[Z,ue]of Object.entries(k[d])){ue.visible=Z===_;for(let ye of ue.userData.doorPanels||[])ye.visible=ue.visible}for(let Z of Y[d]||[])Z.visible=!A}function Kf(d){let _=`${Ui[d]}:${gi[d]}`;if(!Wr[d][_]){let A=w.map(Z=>Z.group.rotation.y);for(let Z of w)Z.group.rotation.y=0;let b=B[d];b.updateWorldMatrix(!0,!0);let I=b.matrixWorld.clone().invert(),le=new ln;b.traverse(Z=>{if(Z.isInstancedMesh){for(let ue=Z;ue!==b;ue=ue.parent)if(!ue.visible)return;Z.boundingBox||Z.computeBoundingBox(),le.union(Z.boundingBox.clone().applyMatrix4(I.clone().multiply(Z.matrixWorld)))}}),Wr[d][_]=le,w.forEach((Z,ue)=>{Z.group.rotation.y=A[ue]})}Ni[d]=Wr[d][_].clone()}function $f(d){return V[d]?.[Ui[d]]?.surfaces||["floor"]}function Fi(d,_={}){let A=B[d],b=_i[d],I=(Me,pe)=>typeof Me=="number"&&Number.isFinite(Me)?Me:pe,le=V[d]&&typeof _.model=="string"&&Object.hasOwn(V[d],_.model)?_.model:b.model;if(le!==Ui[d]){(X.has(d)||["game","image","together"].includes(d))&&!T[d]?.[le]&&Fe(d,le);for(let[Me,pe]of Object.entries(T[d]||{}))pe.visible=Me===le;Ui[d]=le,gi[d]=""}cd(d,typeof _.style=="string"&&Object.hasOwn(o,_.style)?_.style:"oak"),Kf(d);let Z=$f(d);Fn[d]=Z.includes(_.surface)?_.surface:Z.includes(b.surface)?b.surface:Z[0];let ue=Fn[d],ye=ue!=="floor";A.rotation.y=ye?ue==="left-wall"?Math.PI/2:0:gt.degToRad((I(_.rotation,b.rotation)%360+360)%360);let Ue=Ni[d].clone().applyMatrix4(new st().makeRotationY(A.rotation.y));A.position.x=br(gt.clamp(I(_.x,b.x),a.minX-Ue.min.x,a.maxX-Ue.max.x)),A.position.z=br(gt.clamp(I(_.z,b.z),a.minZ-Ue.min.z,a.maxZ-Ue.max.z)),ue==="back-wall"&&(A.position.z=a.minZ-Ue.min.z),ue==="left-wall"&&(A.position.x=a.minX-Ue.min.x);let ae=ue==="left-wall"&&A.position.z+Ue.max.z>a.lowWallStart?a.lowWallTop:a.wallTop;A.position.y=ye?br(gt.clamp(I(_.y,b.y||2.8),.22-Ue.min.y,ae-Ue.max.y)):0,A.updateWorldMatrix(!0,!0),vt=!0,J.shadowMap.needsUpdate=!0}function Uc(d){let _=B[d],A=Ni[d],b=A.getCenter(new N),I=A.getSize(new N).multiplyScalar(.5),le=_.localToWorld(b),Z=_.rotation.y;return{center:le,half:I,axes:[new N(Math.cos(Z),0,-Math.sin(Z)),new N(Math.sin(Z),0,Math.cos(Z))]}}function Qf(d,_){let A=_.center.clone().sub(d.center);return Math.abs(A.y)>=d.half.y+_.half.y-.025?!1:[...d.axes,..._.axes].every(b=>{let I=le=>le.half.x*Math.abs(le.axes[0].dot(b))+le.half.z*Math.abs(le.axes[1].dot(b));return Math.abs(A.dot(b))<I(d)+I(_)-.025})}function ea(d,_=Object.keys(P)){let A=po(d),b=Fn[d];if(A.min.x<a.minX-.015||A.max.x>a.maxX+.015||A.min.z<a.minZ-.015||A.max.z>a.maxZ+.015||A.min.y<-.1||A.max.y>a.wallTop+.015)return"超出房间边界";if(b==="left-wall"&&A.max.z>a.lowWallStart&&A.max.y>a.lowWallTop+.015)return"此处是矮墙，挂件需要降低或移向里侧";if(b==="back-wall"&&A.max.x>-5.55&&A.min.x<-1.55&&A.max.y>1.38&&A.min.y<4.55)return"这里是窗户，请移到窗边的实墙";if(d!=="rug"&&A.max.x>3.62&&A.max.z>.08&&A.min.z<2.65&&A.min.y<3.48)return"请给房门留出开合空间";if(d==="rug")return"";let I=Uc(d),le=_.find(Z=>Z!==d&&Z!=="rug"&&B[Z]?.visible&&Qf(I,Uc(Z)));return le?`与${P[le]}重叠`:""}function mo(d,_,A){if(Fi(d,_),!ea(d,A))return!0;let b=B[d].position.clone(),I=Fn[d],le=[],Z=I!=="floor";for(let ue=-48;ue<=48;ue++)for(let ye=Z?-19:-40;ye<=(Z?19:40);ye++)le.push([ue,ye]);le.sort((ue,ye)=>ue[0]**2+ue[1]**2-ye[0]**2-ye[1]**2);for(let[ue,ye]of le){let Ue=I==="left-wall"?b.x:b.x+ue*.25,ae=I==="back-wall"?b.z:b.z+(Z?-ue:ye)*.25;if(Fi(d,{..._,x:Ue,z:ae,y:Z?b.y+ye*.25:0}),!ea(d,A))return!0}return!1}function Nc(d,_,A=!1){let b=zn().items[d];if(oe.has(d))return Fi(d,_),!0;Fi(d,_);let I=ea(d);return Oi=!1,Kn="",I&&!(A&&mo(d,_))?(Fi(d,b),Oi=!0,Kn=`${I}，已保留原位。可先移动家具再切换外观。`,!1):(I&&(Kn="已放到附近的空位，可继续拖动微调。"),B[d].visible=!0,se.delete(d),!0)}function Jr(d){let _=d?.version===1&&d.items&&typeof d.items=="object"?d.items:_i;for(let b of Object.keys(H))delete H[b];for(let[b,I]of Object.entries(_))/^ext-[0-9a-f]{24}$/.test(b)&&!P[b]&&I&&typeof I=="object"&&(H[b]=structuredClone(I));Qs(d?.version===1?d.houseStyle:"classic"),Kn="",Oi=!1,qs=[];let A=[];for(let b of Object.keys(P)){B[b].visible=!0,se.delete(b),oe.delete(b);let I=_[b]&&typeof _[b]=="object"?_[b]:_i[b];if(Fi(b,I),I.stored===!0){oe.add(b),B[b].visible=!1;continue}if(ea(b,A)&&(qs.push(b),!mo(b,I,A)&&!mo(b,_i[b],A))){if(X.has(b)){Fi(b,I),B[b].visible=!1,se.add(b);continue}if(_!==_i){Jr(null),Kn="原布局没有足够空位，已恢复默认布置；浏览器中的原记录仍保留。",Sr();return}}A.push(b)}qs.length&&(Kn=`已为${qs.map(b=>P[b]).join("、")}避让重叠或门窗；保存后记住新位置。`),Sr(),kt()}function hd(d){H[d]=zn().items[d],B[d].traverse(_=>{if(!_.isInstancedMesh)return;let A=$.indexOf(_);A>=0&&$.splice(A,1),W-=_.count,_.dispose()}),B[d].removeFromParent();for(let _ of[B,P,R,V,k,T,gi,Ui,Fn,_i,Ni,Wr])delete _[d];X.delete(d),se.delete(d),oe.delete(d)}function ud(d){if(An||!Array.isArray(d))return;if(ot){te=structuredClone(d);return}let _=d.filter(b=>/^ext-[0-9a-f]{24}$/.test(b?.id)),A=new Set(_.map(b=>b.id));for(let b of X.keys())A.has(b)||hd(b);for(let b of _){let I=b.id,le=["pedestal","camera","telephone","chess","gallery","monitor"].includes(b.model)?b.model:"pedestal",Z={...b,model:le,accent:/^#[0-9a-f]{6}$/i.test(b.accent)?b.accent:u.teal},ue=X.get(I);if(ue&&ue.model!==le&&hd(I),X.set(I,Z),P[I]=String(Z.label||"拓展家具"),R[I]={label:P[I]},B[I]){if(ue.accent!==Z.accent){let ae=gi[I];gi[I]="",cd(I,ae)}continue}let ye=le==="gallery"?i:["floor"];V[I]={[le]:{label:P[I],surfaces:ye}},B[I]=Q(I),k[I]={},Wr[I]={},Ui[I]=le,gi[I]="",Fn[I]=ye[0],_i[I]={x:0,y:le==="gallery"?3.4:0,z:le==="gallery"?-4.2:.5,rotation:0,style:"oak",model:le,surface:ye[0]},Fe(I,le);let Ue=H[I]||_i[I];Ue.stored===!0?(Fi(I,Ue),B[I].visible=!1,oe.add(I)):mo(I,Ue,Object.keys(P).filter(ae=>ae!==I))||(Fi(I,Ue),B[I].visible=!1,se.add(I)),delete H[I]}P[Wt]||(Wt="lounge"),R[Mn]||(Mn="overview"),Ze="",jr=null,J.shadowMap.needsUpdate=!0,vt=!0,S.dataset.voxelCount=String(W),Sr(),kt()}function ta(d){let _=Ys[d];_&&(_.removeFromParent(),_.geometry.dispose(),_.material.map?.dispose(),_.material.dispose(),delete Ys[d])}function dd(d,_,A){ta(d),_.colorSpace=Xt;let b=d==="photo",I=new Et(new vn(b?.47:.78,b?.69:.62),new cn({map:_,side:En}));I.userData={station:b?"art":"desk",roomObject:structuredClone(A.ref),title:A.title},b?(I.position.set(0,0,.16),B.art.add(I)):(I.rotation.x=-Math.PI/2,I.rotation.z=-.12,I.position.set(.96,1.45,.27),B.desk.add(I)),Ys[d]=I,vt=!0,kt()}function go(d){let _=d?.ref;return _?[_.kind,_.id,_.revision,_.scope].join("\0"):""}function em(d){if(!(An||!d)){if(Object.hasOwn(d,"work")&&go(d.work)!==go(Zs.work)&&(Zs.work=d.work,ta("work"),d.work)){let _=document.createElement("canvas");_.width=520,_.height=410;let A=_.getContext("2d");A.fillStyle="#fff4d8",A.fillRect(0,0,520,410),A.fillStyle="#3e514a",A.font="bold 30px sans-serif",A.fillText(String(d.work.title||"原稿").slice(0,14),32,62),A.font="24px sans-serif";let b=[...String(d.work.summary||"")];for(let I=0;I<5;I++)A.fillText(b.slice(I*17,I*17+17).join(""),32,125+I*46);dd("work",new ri(_),d.work)}if(Object.hasOwn(d,"photo")&&(go(d.photo)!==go(Zs.photo)||d.photo?.image!==Zs.photo?.image)){let _=++Rc;Zs.photo=d.photo,ta("photo");let A=d.photo;if(A&&/^data:image\/(jpeg|png);base64,/.test(A.image||"")){let b=new Image;b.onload=()=>{if(An||_!==Rc)return;let I=document.createElement("canvas");I.width=470,I.height=690;let le=I.getContext("2d"),Z=Math.min(450/b.width,670/b.height);le.fillStyle="#fff4d8",le.fillRect(0,0,470,690),le.drawImage(b,(470-b.width*Z)/2,(690-b.height*Z)/2,b.width*Z,b.height*Z),dd("photo",new ri(I),A)},b.src=A.image}}vt=!0,kt()}}function tm(d){if(!ot)return;let _=zn(),A={..._.items[Wt],...d};d.model&&d.model!==_.items[Wt].model&&(delete A.surface,A.y=2.8),Nc(Wt,A,!!(d.model||d.surface||d.style)),Zr(_),kt()}function nm(d){if(!ot||!P[Wt])return;let _=zn(),A=Wt;Oi=!1,Kn="",d?(oe.add(A),se.delete(A),B[A].visible=!1):(oe.delete(A),Nc(A,_.items[A],!0)||(_.items[A].stored&&oe.add(A),B[A].visible=!1,Kn="房间暂时没有合适空位，仍保留在收纳中。可先移动或收纳其他家具。")),Ze="",J.shadowMap.needsUpdate=!0,vt=!0,Zr(_),kt()}function Oc(d){!ot||!P[d]||(Wt=d,Ze="",Kn="",Oi=!1,Sr(),kt())}function im(d){if(sa(),!d&&Ct){let _=Ct;Ct=null,Bi(1),Qs(_.target),_.onCommitted?.()}if(ot=!!d,pn.visible=ot,mi=[],Di=[],Mn="overview",Ze="",na("overview"),Sr(),!ot&&te){let _=te;te=null,ud(_)}uo(0),E.onAtmosphereChange?.(),kt()}function rm(d=!1){if(!ot)return;let _=d?Di:mi,A=d?mi:Di;_.length&&(A.push(zn()),Jr(_.pop()))}function _o(){return{position:[13.5,12.8,16.2],target:[0,1.15,.5],fov:38}}let vo=null;function Bi(d){if(An)return;let _=gt.clamp(Number(d)||0,0,1);if(_>=1)J.clippingPlanes=Ye,U<1&&(J.shadowMap.enabled=We,J.shadowMap.needsUpdate=!0);else{let A=Ct?18.5:5.05;Le.constant=gt.lerp(-1.05,A,_),J.clippingPlanes=[Le],J.shadowMap.enabled&&(J.shadowMap.enabled=!1)}U=_,vt=!0}function pd(d){if(An)return;let _=gt.clamp(Number(d)||0,0,1);Ot&&Kr(),vo=_<1?_:null,Mn="overview",Ze="",xt=null,Fc(),Jn=!0;let A=yo(_o());j.position.copy(A.t).lerp(A.p,.38+.62*_),ie.target.copy(A.t),j.fov=A.fov,j.updateProjectionMatrix(),kt()}function fd(d){if(d==="overview")return ot?{position:[7.8,17,12.8],target:[0,0,0],fov:40}:_o();let _=B[d];if(!_||!_.visible)return _o();let A=Ni[d]?.getCenter(new N)||new N(0,1.6,0),b=_.localToWorld(A),I=new N(2.7,3.4,7.2).applyAxisAngle(new N(0,1,0),_.rotation.y);return{position:ia(b.clone().add(I)).toArray(),target:b.toArray(),fov:40,focusId:d}}function md(){return[...$,...Object.values(Ys),...ot?[]:ci.meshes].filter(d=>{for(let _=d;_;_=_.parent)if(!_.visible)return!1;return!0})}function yo(d){let _=new N(...d.position),A=new N(...d.target);if(j.aspect<1.25&&_.sub(A).multiplyScalar(1+(1.25-j.aspect)*.64).add(A),d.focusId){ne.updateMatrixWorld(!0);let b=md(),I=_.clone().sub(A),le=[[0,0],[-.5,0],[.5,0],[-.9,1],[.9,1],[0,4]],Z=po(d.focusId).getSize(new N).multiplyScalar(.37),ue=[A,A.clone().add(new N(Z.x,0,0)),A.clone().add(new N(-Z.x,0,0)),A.clone().add(new N(0,0,Z.z)),A.clone().add(new N(0,0,-Z.z)),A.clone().add(new N(0,Z.y,0))],ye=-1;for(let[Ue,ae]of le){let Me=ia(I.clone().applyAxisAngle(new N(0,1,0),Ue).add(A));Me.y+=ae;let pe=0;for(let Re of ue){Ac.set(Me,Re.clone().sub(Me).normalize()),Ac.far=Me.distanceTo(Re)+.1;let ct=Ac.intersectObjects(b,!1)[0];(!ct||ct.object.userData.station===d.focusId)&&pe++}if(pe>ye&&(ye=pe,_.copy(Me)),pe===ue.length)break}}return{p:_,t:A,fov:d.fov||38}}function Fc(){Jn||(ie.enableDamping=!1,ie.update(),ie.enableDamping=!0),j.up.set(0,1,0)}function gd(d,_=1.45){let A=yo(d);if(Fc(),Jn=!0,Ze="",Rn||j.position.distanceToSquared(A.p)<1e-5)j.position.copy(A.p),ie.target.copy(A.t),j.fov=A.fov,j.updateProjectionMatrix(),xt=null;else{let b=j.position.clone(),I=ie.target.clone(),le=b.clone().lerp(A.p,.5),Z=b.distanceTo(A.p);le.y+=Math.min(4,Z*.26),le.x+=Math.min(2.8,Z*.18),xt={curve:new Pr([b,le,A.p]),start:I,end:A.t,fov:j.fov,endFov:A.fov,duration:_,time:0}}kt()}function Kr(){Ot=!1,vt=!0,E.onTourChange?.(!1),E.onShotChange?.("自由观察"),E.onAtmosphereChange?.()}function na(d){vo=null,Kr(),Ze="",Mn=R[d]?d:"overview",E.onFocusChange?.(Mn),gd(fd(Mn))}function sm(d){if(d==="resident"&&!ot){ci.greet(j.position),E.onResidentSelect?.();return}if(d==="entrance"){Td();return}if(ot){Oc(d);return}R[d]&&(na(d),E.onSelect?.(d==="art"?"album":d))}function ia(d){d.x=Math.max(-5.25,d.x),d.z=Math.max(-3.85,d.z),d.y=Math.max(1.35,d.y);for(let _ of Object.keys(P).filter(A=>A!=="rug"&&B[A].visible)){let A=po(_).expandByScalar(.3);d.x>A.min.x&&d.x<A.max.x&&d.z>A.min.z&&d.z<A.max.z&&(d.y=Math.max(d.y,A.max.y+.3))}return d}function Bc(){On=(On+1)%g.length;let d=g[On],_=B[d.anchor],A=new N(...d.target);_&&_.localToWorld(A);let b=d.points.map(Z=>{let ue=new N(...Z);return _&&_.localToWorld(ue),ia(ue)});Fc(),Jn=!0;let I=j.position.clone(),le=I.clone().lerp(b[0],.5);le.y=Math.max(I.y,b[0].y)+1,xt={curve:new Pr([I,le,...b],!1,"centripetal"),start:ie.target.clone(),end:A,fov:j.fov,fovs:[j.fov,j.fov,...d.fovs],endFov:d.fovs.at(-1),duration:d.seconds,time:0,bank:d.bank,cinematic:!0},E.onShotChange?.(d.label),uo(0),E.onAtmosphereChange?.()}function am(){return Ot?(Kr(),xt=null,!1):ot?!1:Rn?(E.onShotChange?.("已开启减少动态效果 · 可手动选择家具"),!1):(Ot=!0,Ze="",Mn="overview",On=-1,E.onTourChange?.(!0),E.onFocusChange?.("overview"),Bc(),!0)}function ra(){let d=Ze==="resident"?{label:ci.getState().label+" · 查看角色"}:Ze==="entrance"?{label:qt?"关上房门":"打开房门"}:ot&&P[Ze]?{label:P[Ze]}:R[Ze];Nn.hidden=!d||Ot||!!fn||!!xt,!Nn.hidden&&(Nn.textContent=!ot&&jr?.title?jr.title:d.label,Nn.style.left=`${Math.max(10,Math.min(Tt-138,ao.x+14))}px`,Nn.style.top=`${Math.max(10,Math.min(Cn-38,ao.y-36))}px`)}function kt(d=!0){if(An)return;let _=!Jn&&ie.enabled?ie.update():!1;if(!d&&!_&&!vt)return;vt=!1;let A=ot?Wt:Ze,b=B[A];zt.visible=!!b?.visible&&!Ot&&!xt&&(ot||!fn),zt.material.opacity=ot?.85:.32,zt.material.color.set(Oi&&ot?"#ca6859":"#dfb55d"),b&&zt.box.copy(Ni[A]?po(A):new ln().setFromObject(b)).expandByScalar(.035),Jn&&j.lookAt(ie.target),J.render(ne,j),ra()}function _d(d){if(!oi||An)return;let _=Math.max(0,(d-Ii)/1e3);if(Ii=d,xt){vt=!0,xt.time+=_;let I=Math.min(1,xt.time/xt.duration),le=xt.cinematic?I:gt.clamp(I*I*I*(I*(I*6-15)+10),0,1);if(j.position.copy(xt.curve.getPointAt(le)),xt.cinematic&&ia(j.position),ie.target.lerpVectors(xt.start,xt.end,xt.cinematic?Math.min(1,I*2.6)**.7:le),xt.fovs){let Z=I*(xt.fovs.length-1),ue=Math.min(xt.fovs.length-2,Math.floor(Z));j.fov=gt.lerp(xt.fovs[ue],xt.fovs[ue+1],Z-ue)}else j.fov=gt.lerp(xt.fov,xt.endFov,le);j.up.set(Math.sin(Math.PI*I)*(xt.bank||0),1,0).normalize(),j.updateProjectionMatrix(),I>=1&&(xt=null,Ot&&Bc())}let A=Li;Li=Rn?Mn==="wardrobe"?1:0:gt.damp(Li,Mn==="wardrobe"?1:0,5,_),Math.abs(A-Li)>1e-4&&(vt=!0,J.shadowMap.needsUpdate=!0);for(let I of w)I.group.rotation.y=I.side*Li*1.65;let b=Yt;Yt=Rn?Number(qt):gt.damp(Yt,Number(qt),4.5,_),Pi.rotation.y=Yt===0?0:-Yt*Math.PI*.55,Math.abs(b-Yt)>1e-4&&(vt=!0,J.shadowMap.needsUpdate=!0),Zf(_),uo(_),Cc+=_,ci.tick(_,{paused:ot,reduced:Rn,viewer:j.position})&&(vt=!0,Cc>=.16&&(J.shadowMap.needsUpdate=!0,Cc=0)),kt(!1),Mr=requestAnimationFrame(_d)}function xo(){if(An)return;let d=Math.max(1,S.clientWidth),_=Math.max(1,S.clientHeight);if(!(d===Tt&&_===Cn)){if(Tt=d,Cn=_,j.aspect=Tt/Cn,j.updateProjectionMatrix(),J.setSize(Tt,Cn,!1),vo!==null){pd(vo);return}if(!Ot){let A=yo(fd(Mn));j.position.copy(A.p),ie.target.copy(A.t),j.up.set(0,1,0),j.fov=A.fov,j.updateProjectionMatrix(),Jn=!0,xt=null}kt()}}function zc(d){let _=!!d&&!document.hidden;An||_===oi||(oi=_,oi?(xo(),Ii=performance.now(),Mr=requestAnimationFrame(_d)):(sa(),cancelAnimationFrame(Mr),Mr=0),uo(0),E.onAtmosphereChange?.())}let Ki=null,vd={sunny:{hemi:1,sun:1,fog:1,exp:1,tint:"#ffffff"},cloudy:{hemi:1.15,sun:.7,fog:.85,exp:.95,tint:"#e0e8e8"},rain:{hemi:.85,sun:.4,fog:.5,exp:.85,tint:"#b0c4de"},storm:{hemi:.65,sun:.25,fog:.35,exp:.75,tint:"#708090"},snow:{hemi:1.3,sun:.85,fog:.6,exp:1.1,tint:"#f0f8ff"},mist:{hemi:.75,sun:.45,fog:.25,exp:.9,tint:"#d1ded9"}};function $r(d,_=!1){let A=["night","dusk","dawn"].includes(d)?d:"day";if(!_&&A===Bn&&ne.background)return;A!==Bn&&Zt&&$s(),Bn=A;let b=m[li]||m.classic,I=vd[Pn]||vd.sunny,le=new ke(b.sky[A]||b.sky.day);Pn!=="sunny"&&le.lerp(new ke(I.tint),.3),ne.background=le;let[Z,ue]=b.fog;ne.fog=new Ma(ne.background,Z*I.fog,ue*I.fog),J.toneMappingExposure=b.exposure*I.exp*(A==="night"?.9:1);let ye=A==="night",Ue=A==="dusk",ae=A==="dawn",Me=ye?.92:Ue?1.35:ae?1.5:1.95;Xe.intensity=Me*b.hemiScale*I.hemi,Xe.color.set(b.hemiSky[A]).lerp(new ke(I.tint),.2),Xe.groundColor.set(b.hemiGround[A]);let pe=ye?.55:Ue?3.2:ae?3.4:5;Ve.intensity=pe*b.sunScale*I.sun,Ve.position.set(...ae?[6,6,-3.8]:Ue?[-8,6,3.5]:ye?[-4,8,-2]:[-2.5,7.5,8.2]),Ve.color.set(b.sunColor[A]).lerp(new ke(I.tint),.3),it.intensity=(ye?.4:.55)*b.lampBoost*I.hemi,it.color.set(b.fillTint),dt.intensity=(ye?.24:.34)*(li==="loft"?1.3:1)*I.hemi,dt.color.set(b.rimTint),_t.intensity=(ye?24:5)*b.lampBoost,et.intensity=(ye?18:4)*b.lampBoost,ve.opacity=ye?.12:.26,xr.opacity=Zi.opacity=ye?.32:.62,nt.opacity=li==="coastal"?ye?.08:.11:ye?.09:.13,Un.setWeather(Pn,Bn),S.dataset.weather=Pn,S.dataset.light=Bn,J.shadowMap.needsUpdate=!0,kt()}function kg(d){if(!Ki)return;Ki.elapsed+=d;let _=Math.min(Ki.elapsed/Ki.duration,1),A=_<.5?2*_*_:1-Math.pow(-2*_+2,2)/2,b=Ki.mode==="night",I=Ki.mode==="dusk",le=Ki.mode==="dawn",Z=["rain","storm","snow","mist","cloudy"].includes(Pn),ue=Pn==="mist",ye=Pn==="storm",Ue=Pn==="snow",ae=m[li]||m.classic,Me=new ke(b?Z?"#293a4a":"#253a49":I?Z?"#bab9c1":"#dcc8bb":le?Z?"#c4ced0":"#e0dacc":ue?"#d1ded9":Ue?"#d2e0e5":ye?"#93a8b2":Z?"#bdcec9":"#c7d6d8");ne.background.lerp(Me,A),_>=1&&(Ki=null),vt=!0}function Mo(d){let _=J.domElement.getBoundingClientRect();id.set((d.clientX-_.left)/_.width*2-1,-(d.clientY-_.top)/_.height*2+1),wc.setFromCamera(id,j),ne.updateMatrixWorld(!0);let A=wc.intersectObjects(md(),!1)[0]?.object?.userData;jr=!ot&&A?.roomObject?{ref:A.roomObject,title:A.title}:null;let b=A?.station||"";return R[b]||b==="entrance"||!ot&&b==="resident"||ot&&P[b]?b:""}function yd(d,_){Mo(d);let A=Fn[_],b=B[_],I=A==="back-wall"?new sn(new N(0,0,1),-b.position.z):A==="left-wall"?new sn(new N(1,0,0),-b.position.x):Hf;return wc.ray.intersectPlane(I,new N)}function xd(d){if(d.button===0){if(ot){let _=Mo(d),A=P[_]?yd(d,_):null;P[_]&&A&&(d.stopImmediatePropagation(),d.preventDefault(),xt=null,Oc(_),ie.enabled=!1,bn={id:_,before:zn(),offset:B[_].position.clone().sub(A),pointerId:d.pointerId},J.domElement.setPointerCapture(d.pointerId))}fn={x:d.clientX,y:d.clientY,id:d.pointerId,moved:!1},Ze="",vt=!0,ra()}}function Md(d){if(fn&&Math.hypot(d.clientX-fn.x,d.clientY-fn.y)>7&&(fn.moved=!0),bn&&d.pointerId===bn.pointerId){let _=yd(d,bn.id);_&&fn?.moved&&(_.add(bn.offset),Nc(bn.id,{...zn().items[bn.id],x:Math.round(_.x*4)/4,y:Math.round(_.y*4)/4,z:Math.round(_.z*4)/4}),Sr(),kt());return}if(!fn&&d.pointerType!=="touch"){let _=J.domElement.getBoundingClientRect();ao.x=d.clientX-_.left,ao.y=d.clientY-_.top;let A=Mo(d);Ze!==A&&(vt=!0),Ze=A,J.domElement.style.cursor=Ze?"pointer":"grab",ra()}}function bd(d){if(bn&&d.pointerId===bn.pointerId){let _=bn.before;bn=null,fn=null,ie.enabled=!0,J.domElement.hasPointerCapture(d.pointerId)&&J.domElement.releasePointerCapture(d.pointerId),Zr(_),kt();return}if(fn&&fn.id===d.pointerId&&!fn.moved){let _=Mo(d);!ot&&jr?E.onObjectSelect?.(structuredClone(jr.ref)):_&&sm(_)}fn=null}function sa(){if(bn){let{before:d,pointerId:_}=bn;bn=null,ie.enabled=!0,Jr(d),J.domElement.hasPointerCapture(_)&&J.domElement.releasePointerCapture(_)}fn=null}function bo(d){bn&&d.type==="pointerleave"||(sa(),Ze="",vt=!0,ra())}function Sd(){Kr(),xt=null,Jn=!1,j.up.set(0,1,0),Ze="",ra()}function Td(d,_=!0){let A=typeof d=="boolean"?d:!qt;return A===qt?qt:(qt=A,S.dataset.doorOpen=String(A),vt=!0,Rn&&(Yt=Number(A),Pi.rotation.y=A?-Math.PI*.55:0,J.shadowMap.needsUpdate=!0,kt()),_&&E.onDoorChange?.(A),A)}function kc(d){if(Rn=Tc||d.matches,Rn){if(Kr(),xt=null,Ct){let _=Ct;Ct=null,Bi(1),Qs(_.target),_.onCommitted?.()}na(Mn),ho(0)}vt=!0,kt(),E.onAtmosphereChange?.()}function Ed(d){d.preventDefault(),zc(!1),E.onError?.("显卡连接中断，请重试加载小屋")}let Qn=J.domElement;Qn.addEventListener("pointerdown",xd,!0),Qn.addEventListener("pointermove",Md),Qn.addEventListener("pointerup",bd),Qn.addEventListener("pointercancel",bo),Qn.addEventListener("pointerleave",bo),Qn.addEventListener("webglcontextlost",Ed),ie.addEventListener("start",Sd),Ec?.addEventListener?.("change",kc);let wd=new ResizeObserver(xo);wd.observe(S);let Ad=yo(_o());return j.position.copy(Ad.p),ie.target.copy(Ad.t),$r(E.light),Jr(E.layout),xo(),E.onTourChange?.(!1),E.onShotChange?.("自由观察"),S.dataset.doorOpen="false",{setTheme(d){let _=fo(d);if(ot){let A=zn();Dc(_,()=>Zr(A))}else Dc(_)},focus:na,resize:xo,setActive:zc,setLight:$r,toggleTour:am,setEntranceProgress:pd,setConstructionProgress:Bi,setStationLabels(d){for(let[_,A]of Object.entries(d||{}))P[_]&&typeof A=="string"&&(R[_]={...R[_],label:A});vt=!0,kt()},setReducedMotion(d){Tc=!!d,kc({matches:!!Ec?.matches})},setAtmosphere:Xf,getAmbientState:jf,resetAtmosphere:qf,encounter:ld,nextShot(){return ot||Rn?!1:(Ot=!0,Mn="overview",Ze="",E.onTourChange?.(!0),Bc(),!0)},getLayout:zn,setEditing:im,selectFurniture:Oc,updateFurniture:tm,setFurnitureStored:nm,updateHouseStyle:Jf,undoEdit:rm,toggleDoor:Td,setFurnitureCatalog:ud,setRoomTraces:em,setResident(d){ci.setContext(d),ci.tick(0,{paused:ot,reduced:Rn}),vt=!0,J.shadowMap.needsUpdate=!0,kt()},getResidentState(){return ci.getState()},focusResident(){if(ot||!ci.getState().visible)return;let d=ci.getState(),{x:_,z:A}=d.position,b=d.appearance?.height||1,I=d.phase==="seated"?d.facing-1.05:d.facing+.6,le=d.phase==="seated"?2.25:3,Z=ia(new N(_+Math.sin(I)*le,2.2*b,A+Math.cos(I)*le));Kr(),gd({position:Z.toArray(),target:[_,1.35*b,A],fov:42}),ci.greet(Z)},getCatalog(){return{furniture:{...P},styles:{...o},surfaces:{...n},objectModels:structuredClone(V),houseStyles:Object.fromEntries(Object.entries(p).map(([d,_])=>[d,{..._}])),styleDetails:Object.fromEntries(Object.entries(l).map(([d,_])=>[d,{note:_.note,models:Object.fromEntries(Object.keys(P).map((A,b)=>[A,_.models[b]]))}]))}},setLayout(d){sa(),Ct=null,Bi(1),mi=[],Di=[],Jr(d)},restoreDefaults(){if(!ot)return;let d=zn();Jr({version:1,items:_i}),Zr(d)},reset(){sa(),na("overview")},getState(){return{editing:ot,editId:Wt,houseStyle:li,placementBlocked:Oi,placementMessage:Kn,storedFurniture:[...oe,...se],roomTraces:Object.fromEntries(Object.entries(Ys).map(([d,_])=>[d,structuredClone(_.userData.roomObject)])),layoutRepairs:[...qs],placementIssues:Object.fromEntries(Object.keys(P).filter(d=>B[d].visible).map(d=>[d,ea(d)]).filter(([,d])=>d)),doorOpen:qt,doorAngle:Pi.rotation.y,tourRunning:Ot,shotIndex:On,moving:!!xt,constructionProgress:U,camera:{position:j.position.toArray(),target:ie.target.toArray(),fov:j.fov,up:j.up.toArray()}}},dispose(){An||(zc(!1),An=!0,wd.disconnect(),Ec?.removeEventListener?.("change",kc),Rc++,ta("photo"),ta("work"),ci.dispose(),Un.dispose(),ie.removeEventListener("start",Sd),ie.dispose(),Qn.removeEventListener("pointerdown",xd,!0),Qn.removeEventListener("pointermove",Md),Qn.removeEventListener("pointerup",bd),Qn.removeEventListener("pointercancel",bo),Qn.removeEventListener("pointerleave",bo),Qn.removeEventListener("webglcontextlost",Ed),$.forEach(d=>d.dispose()),re.dispose(),Se.dispose(),rd.forEach(d=>d.dispose()),Ee.dispose(),ve.dispose(),Ie.dispose(),xn.dispose(),xr.dispose(),Zi.dispose(),mt.dispose(),lt.dispose(),yt.dispose(),nt.dispose(),ft.geometry.dispose(),ft.material.dispose(),zt.geometry.dispose(),zt.material.dispose(),pn.geometry.dispose(),pn.material.dispose(),J.dispose(),S.replaceChildren(),r.delete(S))}}}return{mount(S,E={}){if(r.has(S))return r.get(S);let R=x(S,E);return r.set(S,R),R}}})();})();
/**
 * @license
 * Copyright 2010-2026 Three.js Authors
 * SPDX-License-Identifier: MIT
 */
