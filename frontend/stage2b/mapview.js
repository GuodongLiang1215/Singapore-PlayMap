/* Reuses the fixed 2A-M2 basemap lifecycle and SVG/DOM overlay isolation. */
(function(root){
 'use strict';
 const node=(tag,text,cls)=>{const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(cls)el.className=cls;return el;};
 const $=id=>document.getElementById(id);
 function loadScript(url,integrity){return new Promise((resolve,reject)=>{const s=node('script');s.src=url;if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}const timer=setTimeout(()=>{s.remove();reject(Error('地图组件加载超时'));},7000);s.onload=()=>{clearTimeout(timer);resolve();};s.onerror=()=>{clearTimeout(timer);s.remove();reject(Error('地图组件加载失败'));};document.head.append(s);});}
 class TripMap{
  constructor(callbacks){this.callbacks=callbacks;this.map=null;this.plan=null;this.draft=null;this.group=null;this.legLayers=[];this.markers=new Map();this.poiLayer=null;this.poiSignature=null;this.pick=false;this.viewRevision=0;this.catalogue=[];this.showCatalogue=false;this.focusLayer=null;this.controller=null;}
  async init(){
   const css=node('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
   try{
    try{await loadScript('/stage1-static/vendor/leaflet.js');}catch{await loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
    if(!root.L)throw Error('Leaflet不可用');
    const L=root.L;$('map').replaceChildren();
    const map=this.map=L.map('map',{preferCanvas:false,minZoom:10,maxZoom:20,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false,wheelDebounceTime:40}).setView([1.3521,103.8198],11);
    for(const [name,z] of [['pmPOI',410],['pmRoute',450],['pmEndpoints',600]]){map.createPane(name);map.getPane(name).style.zIndex=String(z);}
    map.getPane('pmRoute').style.pointerEvents='none';map.getPane('pmEndpoints').style.pointerEvents='none';
    this.renderer=L.svg({pane:'pmRoute',padding:.2}).addTo(map);this.endpointGroup=L.layerGroup().addTo(map);
    map.on('movestart',()=>{this.viewRevision++;});map.on('click',e=>{if(this.pick){this.setPick(false);this.callbacks.onMapPoint({latitude:e.latlng.lat,longitude:e.latlng.lng,label:'用户地图选点',source:'user_map',confirmed:true});}});
    L.control.scale({imperial:false}).addTo(map);
    let frame=null;
    this.controller=PlayMapMapFix.createBasemapController(L,map,info=>{if(frame!==null)cancelAnimationFrame(frame);frame=requestAnimationFrame(()=>{frame=null;$('basemap-status').textContent=info.message;$('basemap-status').dataset.level=info.level;});});
    this.controller.set($('basemap').value,$('tiles').checked);
    $('basemap').onchange=$('tiles').onchange=()=>this.controller.set($('basemap').value,$('tiles').checked);
    if(root.ResizeObserver){let w=-1,h=-1,scheduled=null;this.observer=new ResizeObserver(entries=>{const r=entries[0]?.contentRect;if(!r||r.width<=0||r.height<=0)return;const nw=Math.round(r.width),nh=Math.round(r.height);if(nw===w&&nh===h)return;w=nw;h=nh;if(scheduled!==null)cancelAnimationFrame(scheduled);scheduled=requestAnimationFrame(()=>{scheduled=null;map.invalidateSize({animate:false,pan:true,debounceMoveend:true});});});this.observer.observe($('map'));}
    this.setDraft(this.draft);this.setCatalogue(this.catalogue,this.showCatalogue);
    if(this.plan){const plan=this.plan,wasStale=this.stale;this.setPlan(plan,false);if(wasStale)this.markStale();}
   }catch(e){$('map-notice').textContent=e.message+'。可继续编辑与计算数字；运行 scripts/prepare_stage1_map.py 后重新打开页面。';const ph=$('map-placeholder');if(ph)ph.textContent='地图组件未就绪。';}
  }
  setPick(active){this.pick=!!active;$('cancel-pick').hidden=!active;$('pick-status').textContent=active?'点击地图选择位置；Esc取消':'未启用地图选点';$('map').classList.toggle('map-picking',!!active);}
  setCatalogue(features,show){
   this.catalogue=features;this.showCatalogue=show;if(!this.map)return;
   const signature=features.map(f=>f.properties.entity_id).join('|');
   if(this.poiLayer&&this.poiSignature===signature){if(show&&!this.map.hasLayer(this.poiLayer))this.poiLayer.addTo(this.map);else if(!show&&this.map.hasLayer(this.poiLayer))this.map.removeLayer(this.poiLayer);return;}
   if(this.poiLayer)this.map.removeLayer(this.poiLayer);this.poiLayer=null;this.poiSignature=signature;
   if(!show)return;
   this.poiLayer=L.geoJSON({type:'FeatureCollection',features},{pointToLayer:(f,ll)=>L.circleMarker(ll,{pane:'pmPOI',radius:6,color:'#fff',weight:1.5,fillColor:f.properties.default_exploration_visible?'#468771':'#999',fillOpacity:.8,bubblingMouseEvents:false}),onEachFeature:(f,l)=>{l.on('click',e=>{if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);this.setPick(false);this.callbacks.onFeature(f);this.popupFeature(f,l.getLatLng());});}}).addTo(this.map);
  }
  popupFeature(feature,ll){
   if(!this.map)return;const p=feature.properties,content=node('div',undefined,'pm-place-popup');content.append(node('strong',p.display_title),node('p','来源代表点，不是已核验入口。','small'));
   const select=node('button','查看 / 加入行程','quiet');select.onclick=()=>{this.callbacks.onFeature(feature);$('selected-place').scrollIntoView({behavior:'auto',block:'nearest'});this.map.closePopup();};content.append(select);
   L.popup({autoPan:false,maxWidth:280}).setLatLng(ll).setContent(content).openOn(this.map);
  }
  setDraft(data){
   this.draft=data;if(!this.map)return;
   const raw=[];if(data?.origin)raw.push({p:data.origin,label:'A',kind:'start',detail:'出发点'});
   for(let i=0;i<(data?.visits.length||0);i++)raw.push({p:data.visits[i].point,label:String(i+1),kind:'visit',detail:'停留 '+(i+1)});
   if(data?.finish_policy==='custom'&&data.finish)raw.push({p:data.finish,label:'终',kind:'end',detail:'结束位置'});
   if(data?.finish_policy==='return_to_start'&&data.origin)raw[0].detail='出发与返程结束点';
   const grouped=new Map();for(const item of raw){const key=item.p.latitude+','+item.p.longitude;if(!grouped.has(key))grouped.set(key,[]);grouped.get(key).push(item);}
   for(const [key,marker] of this.markers){if(!grouped.has(key)){this.endpointGroup.removeLayer(marker);this.markers.delete(key);}}
   for(const [key,items] of grouped){const first=items[0],text=items.map(i=>i.label).join('/'),span=node('span',text,'pm-trip-pin '+first.kind),shell=node('div');shell.append(span);
    const icon=L.divIcon({className:'pm-trip-marker',html:shell.innerHTML,iconSize:[Math.max(28,text.length*9+12),28],iconAnchor:[14,14]});
    let marker=this.markers.get(key);if(!marker){marker=L.marker([first.p.latitude,first.p.longitude],{pane:'pmEndpoints',icon,keyboard:true,bubblingMouseEvents:false}).addTo(this.endpointGroup);this.markers.set(key,marker);}else marker.setIcon(icon);
    const content=node('div');for(const it of items)content.append(node('strong',it.detail),node('p',it.p.label));content.append(node('p','编号对应当前编辑清单；位置尚未核验为入口。','small'));marker.bindPopup(content,{autoPan:false});
   }
   $('fit-stops').disabled=!raw.length;
  }
  setPlan(plan,fit=true){
   if(!this.map){this.plan=plan;this.stale=false;return;}
   const next=L.layerGroup(),layers=[];
   try{for(const leg of plan.legs){
    if(!leg.geometry){layers.push(null);continue;}
    const coords=leg.geometry.coordinates;if(leg.geometry.type!=='LineString'||!Array.isArray(coords)||coords.length<2||coords.some(p=>p.length!==2||!Number.isFinite(p[0])||!Number.isFinite(p[1])))throw Error('路段线形不完整。');
    const line=L.geoJSON(leg.geometry,{pane:'pmRoute',renderer:this.renderer,interactive:false,style:{pane:'pmRoute',renderer:this.renderer,color:'#175db5',weight:5,opacity:.9}}).addTo(next);layers.push(line);
   }next.addTo(this.map);}catch(e){if(this.map.hasLayer(next))this.map.removeLayer(next);throw Error('新行程绘制失败，保留上一份地图结果。'+e.message);}
   if(this.group)this.map.removeLayer(this.group);this.group=next;this.legLayers=layers;this.plan=plan;this.stale=false;$('fit-route').disabled=false;
   if(fit)this.fitRoute();
  }
  markStale(){this.stale=true;for(const l of this.legLayers)if(l)l.setStyle({color:'#848d89',opacity:.5,dashArray:'7 7'});}
  clearPlan(){if(this.group&&this.map)this.map.removeLayer(this.group);this.group=null;this.plan=null;this.legLayers=[];this.stale=false;$('fit-route').disabled=true;}
  bounds(points,maxZoom=17){if(!this.map||!points.length)return;this.map.stop();this.map.fitBounds(points,{padding:[35,35],maxZoom,animate:false});}
  fitNation(){if(this.map){this.map.stop();this.map.setView([1.3521,103.8198],11,{animate:false});}}
  fitRoute(){if(!this.plan)return;let points=this.plan.legs.flatMap(l=>l.geometry?l.geometry.coordinates.map(p=>[p[1],p[0]]):[]);if(!points.length)points=[this.plan.origin,...this.plan.visits.map(v=>v.point)].map(p=>[p.latitude,p.longitude]);this.bounds(points);}
  fitDraft(){if(!this.draft)return;const points=[this.draft.origin,...this.draft.visits.map(v=>v.point),this.draft.finish_policy==='custom'?this.draft.finish:null].filter(Boolean);this.bounds(points.map(p=>[p.latitude,p.longitude]));}
  focusPoint(point){if(!this.map)return;this.map.stop();this.map.setView([point.latitude,point.longitude],16,{animate:false});if(this.focusLayer)this.map.removeLayer(this.focusLayer);this.focusLayer=L.circleMarker([point.latitude,point.longitude],{pane:'pmRoute',renderer:this.renderer,radius:12,weight:2,color:'#cc8e2f',fillOpacity:0,interactive:false}).addTo(this.map);}
  focusLeg(index){const l=this.plan?.legs[index];if(!l)return;if(l.geometry)this.bounds(l.geometry.coordinates.map(p=>[p[1],p[0]]));else this.focusPoint(l.origin);}
 }
 root.PlayMapTripMap=TripMap;
})(window);
