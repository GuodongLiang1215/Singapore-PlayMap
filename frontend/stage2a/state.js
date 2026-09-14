/* No persistence. This state is scoped to the page, not a full itinerary session. */
(function(root){
 'use strict';
 class RouteState {
  constructor(){this.origin=null;this.destination=null;this.mode='walk';this.revision=0;this.sequence=0;this.lastSuccess=null;}
  change(target,value){if(!['origin','destination','mode'].includes(target))throw Error('Invalid state target');this[target]=value;this.revision++;this.sequence++;}
  swap(){const a=this.origin;this.origin=this.destination;this.destination=a;this.revision++;this.sequence++;}
  clear(){this.origin=null;this.destination=null;this.lastSuccess=null;this.revision++;this.sequence++;}
  begin(){if(!this.origin||!this.destination)throw Error('请先选择起终点');return {sequence:++this.sequence,revision:this.revision};}
  accepts(ticket,result){return ticket.sequence===this.sequence&&ticket.revision===this.revision&&result.revision===this.revision;}
  save(ticket,result){if(!this.accepts(ticket,result))return false;this.lastSuccess=result;return true;}
  payload(){return {origin:this.origin,destination:this.destination,mode:this.mode,revision:this.revision};}
 }
 root.PlayMapRouteState=RouteState;
 if(typeof module!=='undefined'&&module.exports)module.exports=RouteState;
})(typeof window==='undefined'?globalThis:window);
