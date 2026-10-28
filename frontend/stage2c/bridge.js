/* Additive bridge: the tested Stage2B state, map and calculator remain unchanged. */
(function(){'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 function notify(kind,previous,previousData){window.dispatchEvent(new CustomEvent('playmap:edit',{detail:{kind,previous,previousData,revision:trip.revision}}));}
 const change=trip.change.bind(trip),undo=trip.undo.bind(trip),clear=trip.clear.bind(trip);
 trip.change=function(fn){const prev=trip.revision,before=clone(trip.data);change(fn);notify('change',prev,before);};
 trip.undo=function(){const prev=trip.revision,before=clone(trip.data),result=undo();if(result)notify('undo',prev,before);return result;};
 trip.clear=function(){const prev=trip.revision,before=clone(trip.data);clear();notify('clear',prev,before);};
 window.PlayMapChatBridge={
  snapshot(){
   // Blur events usually sync first; validate the complete editor regardless.
   if(syncEditor()){renderTrip();invalidate();}
   const d=clone(trip.data);if(d.finish_policy!=='custom')d.finish=null;
   return {session_id:trip.sessionId,revision:trip.revision,draft:d};
  },
  currentRevision:()=>trip.revision,
  currentSequence:()=>trip.sequence,
  currentSession:()=>trip.sessionId,
  hasOrigin:()=>!!trip.data.origin,
  // Resolves only after the user answers the device-location card; true means an
  // origin was adopted. Never sets an origin on its own.
  proposeDeviceOrigin:()=>proposeDeviceOrigin(),
  currentData:()=>clone(trip.data),
  apply(draft,expected){
   // Do not sync here: applying a pending manual edit would change its ledger
   // inside the chat-adoption transaction. Require a fresh proposal instead.
   if(editorChanged(readEditor()))throw Error('The editor has unsaved changes. Apply those edits before adopting a new chat draft.');
   if(trip.revision!==expected)throw Error('The itinerary was edited manually. Send a new message; the older chat draft cannot overwrite it.');
   cancelCalculation();
   trip.change(d=>{for(const key of Object.keys(d))delete d[key];Object.assign(d,clone(draft));});
   formInvalid=false;cancelReplacement();timeInputs();renderTrip();invalidate();
   clearTimeout(autoTimer);reportError(null);return trip.revision;
  },
  calculate:()=>calculate(false),
  focus:point=>mapView.focusPoint(point),
  diagnostic:()=>T.diagnostic(trip.lastSuccess,trip.revision),
  activePlan:()=>trip.lastSuccess?.revision===trip.revision?trip.lastSuccess:null,
 };
})();
