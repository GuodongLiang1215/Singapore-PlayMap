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
   return fail('PERMISSION_DENIED','Location permission was denied. Allow it in the browser address-bar settings and retry, or select a point manually.');
  if(error&&error.code===(codes.POSITION_UNAVAILABLE??2))
   return fail('POSITION_UNAVAILABLE','The device cannot determine your location, possibly due to weak signal or being indoors. Retry later or select a point manually.');
  if(error&&error.code===(codes.TIMEOUT??3))
   return fail('TIMEOUT','Location request timed out. Retry or select a point manually.');
  return fail('LOCATION_FAILED','No location was obtained. Select a point manually.');
 }

 function inEnvelope(latitude,longitude){
  return longitude>=ENVELOPE.west&&longitude<=ENVELOPE.east
      &&latitude>=ENVELOPE.south&&latitude<=ENVELOPE.north;
 }

 function read(options){
  const {timeout=10000,maximumAge=60000,enableHighAccuracy=true}=options||{};
  return new Promise((resolve,reject)=>{
   if(!root.isSecureContext)
    return reject(fail('INSECURE_CONTEXT','Browser location requires HTTPS. Open the HTTPS address or select a point manually.'));
   if(!root.navigator||!root.navigator.geolocation)
    return reject(fail('UNSUPPORTED','This browser does not support geolocation. Select a point manually.'));
   let settled=false;
   root.navigator.geolocation.getCurrentPosition(
    position=>{
     if(settled)return;settled=true;
     const c=position&&position.coords;
     const latitude=c&&c.latitude,longitude=c&&c.longitude;
     if(!Number.isFinite(latitude)||!Number.isFinite(longitude))
      return reject(fail('LOCATION_FAILED','Invalid location coordinates were returned and were not used. Select a point manually.'));
     // A device may report null/NaN accuracy. Absent is reported as absent,
     // never silently replaced with an optimistic number.
     const raw=c.accuracy;
     const accuracy=Number.isFinite(raw)&&raw>=0?raw:null;
     if(!inEnvelope(latitude,longitude))
      return reject(fail('OUTSIDE_SERVICE_AREA','Your device location is outside the Singapore service area. The itinerary is unchanged; select a point manually.'));
     resolve({latitude,longitude,accuracy_m:accuracy,
              coarse:accuracy===null||accuracy>COARSE_ACCURACY_M});
    },
    error=>{if(!settled){settled=true;reject(classify(error));}},
    {enableHighAccuracy,timeout,maximumAge});
  });
 }

 function describe(reading){
  if(!reading)return '';
  if(reading.accuracy_m===null)return 'The device did not report an accuracy radius. Location accuracy is unknown.';
  const value=reading.accuracy_m>=1000
   ?(reading.accuracy_m/1000).toFixed(1)+' km'
   :Math.round(reading.accuracy_m)+' m';
  return 'Device-reported accuracy: approximately ±'+value+(reading.coarse?'. Accuracy is low; check before using it as the start point.':'.');
 }

 root.PlayMapLocate={read,describe,classify,inEnvelope,ENVELOPE,COARSE_ACCURACY_M};
})(window);
