/* Additive bridge: the tested Stage2B state, map and calculator remain unchanged. */
(function(){'use strict';
 const clone=x=>JSON.parse(JSON.stringify(x));
 function notify(kind,previous){window.dispatchEvent(new CustomEvent('playmap:edit',{detail:{kind,previous,revision:trip.revision}}));}
 const change=trip.change.bind(trip),undo=trip.undo.bind(trip),clear=trip.clear.bind(trip);
 trip.change=function(fn){const prev=trip.revision;change(fn);notify('change',prev);};
 trip.undo=function(){const prev=trip.revision,result=undo();if(result)notify('undo',prev);return result;};
 trip.clear=function(){const prev=trip.revision;clear();notify('clear',prev);};
 window.PlayMapChatBridge={
  snapshot(){
   // Blur/change events usually sync the UI first; explicitly reject invalid time forms.
   const time=readTime();if(formInvalid)throw Error('请先修正左侧尚未完成的时间输入，再发送聊天。');
   if(JSON.stringify(time)!==JSON.stringify(trip.data.time)){trip.setTime(time);renderTrip();invalidate();}
   const d=clone(trip.data);if(d.finish_policy!=='custom')d.finish=null;
   return {session_id:trip.sessionId,revision:trip.revision,draft:d};
  },
  currentRevision:()=>trip.revision,
  currentSession:()=>trip.sessionId,
  currentData:()=>clone(trip.data),
  apply(draft,expected){
   if(formInvalid)throw Error('左侧时间输入尚未完成，请先修正后再采用草案。');
   readTime();
   if(trip.revision!==expected)throw Error('你已经手动修改行程；旧聊天草案不能覆盖新输入，请重新发送。');
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
