/* Browser geolocation as a PROPOSAL, never an origin.

   A device reading is a measurement with its own error radius. This module only
   reads and classifies it: it never writes to the itinerary, never rounds a
   coordinate, and never marks anything confirmed. Confirmation stays a user act.
*/
(function(root){
 'use strict';
 // Mirrors the backend SG_REQUEST_ENVELOPE. Checked here so an out-of-range
 // reading produces a true message instead of the backend's lat/lon-order error.
 const ENVELOPE={west:103.4,south:0.9,east:104.7,north:1.7};
 // Above this the reading is still offered, but labelled as too coarse to trust
 // as a start point. It is a project threshold, not a device specification.
 const COARSE_ACCURACY_M=100;

 function fail(code,message){const e=Error(message);e.code=code;return e;}

 function classify(error){
  const codes=root.GeolocationPositionError||{};
  if(error&&error.code===(codes.PERMISSION_DENIED??1))
   return fail('PERMISSION_DENIED','浏览器未授权定位。可在地址栏的权限设置中允许后重试，或继续手动选点。');
  if(error&&error.code===(codes.POSITION_UNAVAILABLE??2))
   return fail('POSITION_UNAVAILABLE','设备暂时无法确定位置（可能在室内或无定位信号）。请稍后重试，或手动选点。');
  if(error&&error.code===(codes.TIMEOUT??3))
   return fail('TIMEOUT','定位超时，未取得位置。可以重试，或手动选点。');
  return fail('LOCATION_FAILED','定位未完成，未取得位置。请手动选点。');
 }

 function inEnvelope(latitude,longitude){
  return longitude>=ENVELOPE.west&&longitude<=ENVELOPE.east
      &&latitude>=ENVELOPE.south&&latitude<=ENVELOPE.north;
 }

 function read(options){
  const {timeout=10000,maximumAge=60000,enableHighAccuracy=true}=options||{};
  return new Promise((resolve,reject)=>{
   if(!root.isSecureContext)
    return reject(fail('INSECURE_CONTEXT','浏览器只在 HTTPS 页面提供定位。请用 https 地址打开，或手动选点。'));
   if(!root.navigator||!root.navigator.geolocation)
    return reject(fail('UNSUPPORTED','此浏览器不提供定位接口。请手动选点。'));
   let settled=false;
   root.navigator.geolocation.getCurrentPosition(
    position=>{
     if(settled)return;settled=true;
     const c=position&&position.coords;
     const latitude=c&&c.latitude,longitude=c&&c.longitude;
     if(!Number.isFinite(latitude)||!Number.isFinite(longitude))
      return reject(fail('LOCATION_FAILED','定位返回的坐标无效，未使用。请手动选点。'));
     // A device may report null/NaN accuracy. Absent is reported as absent,
     // never silently replaced with an optimistic number.
     const raw=c.accuracy;
     const accuracy=Number.isFinite(raw)&&raw>=0?raw:null;
     if(!inEnvelope(latitude,longitude))
      return reject(fail('OUTSIDE_SERVICE_AREA','当前定位不在本项目的服务范围内（本项目仅覆盖新加坡）。已保留手动选点，没有改动行程。'));
     resolve({latitude,longitude,accuracy_m:accuracy,
              coarse:accuracy===null||accuracy>COARSE_ACCURACY_M});
    },
    error=>{if(!settled){settled=true;reject(classify(error));}},
    {enableHighAccuracy,timeout,maximumAge});
  });
 }

 function describe(reading){
  if(!reading)return '';
  if(reading.accuracy_m===null)return '设备未报告精度半径，无法判断这个位置有多准。';
  const value=reading.accuracy_m>=1000
   ?(reading.accuracy_m/1000).toFixed(1)+' 公里'
   :Math.round(reading.accuracy_m)+' 米';
  return '设备自报精度约 ±'+value+(reading.coarse?'，误差偏大，建议核对后再用作出发点。':'。');
 }

 root.PlayMapLocate={read,describe,classify,inEnvelope,ENVELOPE,COARSE_ACCURACY_M};
})(window);
