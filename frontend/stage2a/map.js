'use strict';
const $=id=>document.getElementById(id),state=new PlayMapRouteState();
const LABELS={nature:'Parks and nature',tourism:'Attractions, shopping and entertainment',heritage:'Historic places',monument:'Monuments',food:'Food and drink'};
let map=null,tiles=null,poiLayer=null,routeLayer=null,snappedLayer=null,endpointLayer=null,focusLayer=null,basemapControl=null,routeRenderer=null;
const endpointMarkers={origin:null,destination:null};
let poiSignature=null,viewRevision=0,catalogueTimer=null,resizeFrame=null,basemapFrame=null;
let all=[],profile=null,matched=[],listLimit=50,detailSeq=0,pickTarget=null,autoTimer=null,routeController=null;
const searches={origin:{sequence:0,next:null,query:'',controller:null},destination:{sequence:0,next:null,query:'',controller:null}};
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function errText(e){return typeof e==='string'?e:e?.message||'Request failed. Check the connection status.';}
async function api(url,body,signal){const options={cache:'no-store',signal};if(body!==undefined){options.method='POST';options.headers={'Content-Type':'application/json','X-PlayMap-Client':'stage2a'};options.body=JSON.stringify(body);}const r=await fetch(url,options);let d;try{d=await r.json();}catch{throw Error('The server did not return JSON. Check that the application is running.');}if(!r.ok){const x=d.detail;throw Error(typeof x==='string'?x:(x?.message||'Request failed')+(x?.code?' ['+x.code+']':''));}return d;}
async function status(){try{const d=await api('/api/stage2a/status');const ok=d.credentials.configured&&!['expired','invalid_format'].includes(d.credentials.state);$('credential-status').dataset.ready=String(ok);$('credential-status').textContent=ok?'Token configured · Validity is checked when a request is made':'No usable token · Check the OneMap credentials';if(d.catalogue.ready)$('catalogue-status').textContent=`${d.catalogue.entity_count} place groups across Singapore · ${d.catalogue.default_exploration_count} shown by default · Not a count of verified attractions`;else $('catalogue-status').textContent=d.catalogue.message;}catch(e){$('credential-status').textContent=errText(e);}}
function invalidate(){
  if(snappedLayer&&map){map.removeLayer(snappedLayer);snappedLayer=null;}
  if(routeController){routeController.abort();routeController=null;}
  clearTimeout(autoTimer);$('export-route').disabled=true;$('export-status').textContent='';
  if(state.lastSuccess){
    $('route-status').textContent='Inputs changed. The grey route and faded values belong to the previous result and do not apply to the current inputs.';
    $('route-status').className='stale';
    $('route-version').textContent='Previous result'+({walk:'Walk',drive:'Drive',cycle:'Cycle'}[state.lastSuccess.mode]||'')+'Result · Recalculation needed';
    $('route-metrics').classList.add('is-stale');$('route-diagnostic').classList.add('is-stale');
    if(routeLayer)routeLayer.setStyle({color:'#7b807d',dashArray:'7 7',opacity:.5});
  }else{$('route-status').textContent='Selection updated. Calculate the route to continue.';$('route-status').className='';}
  updateFitButtons();
  if(state.lastSuccess&&$('auto-route').checked&&state.origin&&state.destination)autoTimer=setTimeout(calculate,300);
}
function refreshEndpoints(){
  for(const k of ['origin','destination']){
    const p=state[k];$(k+'-selection').textContent=p?`${p.label}\n${p.latitude.toFixed(6)}, ${p.longitude.toFixed(6)} · Selected point, not a verified entrance`:'Not selected';
  }
  updateFitButtons();if(!map)return;
  // DOM markers have native hit targets and do not put a full-screen Canvas
  // above all POIs. Update their coordinates rather than destroy/recreate them.
  if(!endpointLayer)endpointLayer=L.layerGroup().addTo(map);
  for(const k of ['origin','destination']){
    const p=state[k],label=k==='origin'?'A · Start':'B · Destination';
    if(!p){if(endpointMarkers[k])endpointLayer.removeLayer(endpointMarkers[k]);endpointMarkers[k]=null;continue;}
    if(!endpointMarkers[k]){
      const icon=L.divIcon({className:'pm-endpoint-marker',html:'<span class="pm-pin pm-pin-'+k+'">'+(k==='origin'?'A':'B')+'</span>',iconSize:[28,28],iconAnchor:[14,14]});
      endpointMarkers[k]=L.marker([p.latitude,p.longitude],{pane:'pmEndpoints',icon,title:label,keyboard:true,bubblingMouseEvents:false}).addTo(endpointLayer);
    }else endpointMarkers[k].setLatLng([p.latitude,p.longitude]);
    const content=node('div');content.append(node('strong',label),node('p',p.label),node('p','A point you selected, not a verified entrance.','small'));
    endpointMarkers[k].bindPopup(content,{autoPan:false});
  }
}

function choose(k,p){
  state.change(k,{latitude:Number(p.latitude),longitude:Number(p.longitude),label:String(p.label).slice(0,200),source:p.source,confirmed:true});
  refreshEndpoints();invalidate();setPick(null);
  for(const b of $(k+'-results').querySelectorAll('.result'))b.classList.remove('is-selected');
  if(map){map.stop();map.panTo([p.latitude,p.longitude],{animate:false});}
}
function resetSearch(k){const s=searches[k];s.sequence++;if(s.controller)s.controller.abort();s.next=null;$(k+'-more').hidden=true;$(k+'-results').replaceChildren();}
async function search(k,page=1){const s=searches[k],query=$(k+'-query').value.trim();if(!query){$(k+'-results').replaceChildren(node('p','Enter a place, building or postal code.','error'));return;}if(s.controller)s.controller.abort();const controller=new AbortController();s.controller=controller;const seq=++s.sequence;s.query=query;$(k+'-results').replaceChildren(node('p','Searching OneMap…','small'));$(k+'-more').hidden=true;try{const d=await api('/api/stage2a/search',{query,page},controller.signal);if(seq!==s.sequence||$(k+'-query').value.trim()!==query)return;const panel=$(k+'-results');panel.replaceChildren(node('p',`Provider matches: ${d.provider_found} · Page ${d.page}/${d.total_pages||1}. Choose a result.`,'small'));for(const r of d.results){const b=node('button',r.label,'result');b.append(node('small',r.address||'Address unavailable'),node('small',`Use as ${k==='origin'?'start':'destination'} · Entrance not verified`));b.onclick=()=>choose(k,r);panel.append(b);}if(!d.results.length)panel.append(node('p','No usable candidates on this page. Try an English building name, a postal code or a map point. This does not mean the place does not exist.','small'));if(d.invalid_rows_on_page)panel.append(node('p',`Coordinates rejected by validation on this page: ${d.invalid_rows_on_page}.`,'warning'));s.next=d.next_page;$(k+'-more').hidden=s.next===null;}catch(e){if(e.name!=='AbortError'&&seq===s.sequence)$(k+'-results').replaceChildren(node('p',errText(e),'error'));}}
function routeMetrics(r){
  $('route-metrics').hidden=false;$('route-metrics').classList.remove('is-stale');
  $('distance').textContent=(r.distance_m/1000).toFixed(2)+' km';
  $('duration').textContent=(r.duration_s/60).toFixed(1)+' min';
  $('offsets').textContent=`A ${r.endpoint_offsets_m.origin.toFixed(0)} m / B ${r.endpoint_offsets_m.destination.toFixed(0)} m`;
  const box=$('route-warnings');box.replaceChildren();for(const text of r.warnings)box.append(node('p',text,r.endpoint_review_required?'warning':'small'));
  $('route-source').textContent='Route provider: OneMap · Requested mode: '+({walk:'Walk',drive:'Drive',cycle:'Cycle'}[r.mode]||r.mode)+`(${r.mode}) · Calculated by the provider`;
  const d=PlayMapMapFix.diagnostics(r),panel=$('route-diagnostic');
  panel.hidden=false;panel.classList.remove('is-stale');panel.replaceChildren();
  panel.append(node('p',`Straight-line reference: ${(d.straight_line_m/1000).toFixed(2)} km; provider route: ${d.distance_to_straight_ratio===null?'—':d.distance_to_straight_ratio.toFixed(1)} times that distance. The straight line is not a travel route.`,'small'));
  if(d.large_detour_flag)panel.append(node('p','The route is much longer than the straight-line distance. Check the selected points and provider route. The route has been kept unchanged; this check does not prove it is incorrect.','route-check'));
  if(d.geometry_summary_mismatch)panel.append(node('p','The provider distance differs substantially from the returned geometry length. Check the response; the original route is retained.','route-check'));
  $('export-route').disabled=false;
}
async function calculate(){
  clearTimeout(autoTimer);if(routeController)routeController.abort();
  let ticket;try{ticket=state.begin();}catch(e){$('route-status').textContent=errText(e);$('route-status').className='error';return;}
  const controller=new AbortController();routeController=controller;
  const requestViewRevision=viewRevision;
  $('export-route').disabled=true;$('export-status').textContent='';
  $('route-status').textContent='Requesting a route from OneMap. No straight-line route or preset duration is substituted…';$('route-status').className='';
  $('route-version').textContent='Calculating · Revision '+ticket.revision;
  try{
    const r=await api('/api/stage2a/route',state.payload(),controller.signal);
    if(!state.accepts(ticket,r))return;
    if(r.mode!==state.mode)throw Error('The returned mode does not match this request. The result was not shown as a new route.');
    // Prepare the new display before discarding the old route. No async work
    // between this point and saving the accepted state.
    drawRoute(r,{fit:viewRevision===requestViewRevision});
    if(!state.save(ticket,r))return;
    routeMetrics(r);
    const d=PlayMapMapFix.diagnostics(r);
    $('route-status').textContent=r.endpoint_review_required?'Route returned. Check the offsets between its endpoints and your selected points.':d.large_detour_flag?'Route returned with a large detour. Check it; the shortest route has not been established.':'Provider route received between the selected points. Entrance access, opening hours and trip feasibility still need checking.';
    $('route-status').className=(r.endpoint_review_required||d.large_detour_flag)?'warning':'';
    $('route-version').textContent='Current revision '+r.revision+' · '+new Date(r.queried_at).toLocaleTimeString();
    updateFitButtons();
  }catch(e){
    if(e.name==='AbortError'||ticket.sequence!==state.sequence)return;
    $('route-status').textContent=errText(e)+(state.lastSuccess?' The previous route is shown for comparison, not as the result of this request.':' No route was created for this request.');$('route-status').className='error';
    $('route-version').textContent=state.lastSuccess?'Failed · Previous result below'+({walk:'Walk',drive:'Drive',cycle:'Cycle'}[state.lastSuccess.mode]||'')+'Result':'Calculation failed';
    $('route-metrics').classList.add('is-stale');$('route-diagnostic').classList.add('is-stale');
    if(routeLayer)routeLayer.setStyle({color:'#7b807d',dashArray:'7 7',opacity:.5});
  }finally{if(routeController===controller)routeController=null;}
}
function filtered(){const terms=$('catalogue-query').value.normalize('NFKC').toLowerCase().trim().split(/\s+/).filter(Boolean),cat=$('category').value;return all.filter(f=>{const p=f.properties;return ($('include-holds').checked||p.default_exploration_visible)&&(!cat||p.categories.includes(cat))&&terms.every(t=>(p.search_text||'').includes(t));});}
function renderCatalogue(){
  matched=filtered();const show=$('show-pois').checked;
  $('list-count').textContent=`${matched.length} matching groups · ${show?'All matching points shown on the map':'Point layer hidden; catalogue unchanged'} · List loads in batches`;
  const panel=$('catalogue-list');panel.replaceChildren();
  for(const f of matched.slice(0,listLimit)){const p=f.properties,b=node('button',p.display_title,'catalogue-item');b.append(node('small',p.categories.map(c=>LABELS[c]||c).join(' / ')+(p.default_exploration_visible?'':' · Former location / needs review')));b.onclick=()=>showDetail(p.entity_id,true);panel.append(b);}
  if(!matched.length)panel.append(node('p','No local catalogue match. You can still search OneMap above.','small'));
  $('catalogue-more').hidden=listLimit>=matched.length;
  if(!map)return;
  const signature=matched.map(f=>f.properties.entity_id).join('\n');
  if(poiLayer&&poiSignature===signature){
    if(show&&!map.hasLayer(poiLayer))poiLayer.addTo(map);
    if(!show&&map.hasLayer(poiLayer))map.removeLayer(poiLayer);
    return; // List pagination/visibility changes do not rebuild every SVG point.
  }
  if(poiLayer)map.removeLayer(poiLayer);poiLayer=null;poiSignature=signature;
  if(!show)return;
  poiLayer=L.geoJSON({type:'FeatureCollection',features:matched},{
    pointToLayer:(f,ll)=>L.circleMarker(ll,{pane:'pmPOI',radius:6,color:'#fff',weight:1.5,fillColor:f.properties.default_exploration_visible?'#468771':'#999',fillOpacity:.8,bubblingMouseEvents:false}),
    onEachFeature:(f,l)=>{
      l.on('click',e=>{
        if(e.originalEvent)L.DomEvent.stopPropagation(e.originalEvent);
        if(pickTarget){const target=pickTarget;chooseCataloguePoint(target,f);return;}
        openPlacePopup(f,l.getLatLng());showDetail(f.properties.entity_id,false);
      });
    }
  }).addTo(map);
}
function chooseCataloguePoint(target,feature){
  const p=feature.properties;
  if(!p.default_exploration_visible){$('map-notice').textContent='A former location or record awaiting review cannot be used directly as a current destination. Find its current location first.';return;}
  if(!window.confirm('Use the source coordinate for '+p.display_title+' as the '+(target==='origin'?'start':'destination')+'? This is not a verified entrance; an interior park point may not be directly accessible.'))return;
  const [longitude,latitude]=feature.geometry.coordinates;
  choose(target,{latitude,longitude,label:p.display_title+' (representative point)',source:'user_map'});
  if(map)map.closePopup();
}
function openPlacePopup(f,ll){
  const p=f.properties,content=node('div',undefined,'pm-place-popup');
  content.append(node('strong',p.display_title),node('p',p.categories.map(c=>LABELS[c]||c).join(' / ')),node('p',p.address||'No address in the source','small'));
  content.append(node('p',p.default_exploration_visible?'Source representative point, not a verified entrance.':'Former location or status awaiting review. Search for the current location first.','small'));
  const actions=node('div',undefined,'detail-actions');
  if(p.default_exploration_visible){
    for(const [target,label] of [['origin','Set as A (representative point)'],['destination','Set as B (representative point)']]){
      const b=node('button',label,'quiet');b.type='button';b.onclick=()=>chooseCataloguePoint(target,f);actions.append(b);
    }
  }
  const searchButton=node('button','Find an access point','quiet');searchButton.type='button';searchButton.onclick=()=>{
    $('destination-query').value=p.display_title;resetSearch('destination');search('destination');
    $('destination-query').scrollIntoView({behavior:'auto',block:'center'});map.closePopup();
  };
  const more=node('button','View details','quiet');more.type='button';more.onclick=()=>{$('detail').scrollIntoView({behavior:'auto',block:'center'});};
  actions.append(searchButton,more);content.append(actions);
  L.popup({maxWidth:310,autoPan:false,closeButton:true}).setLatLng(ll).setContent(content).openOn(map);
}

async function showDetail(id,fly){const seq=++detailSeq;const panel=$('detail');panel.replaceChildren(node('p','Loading place details…'));try{const d=await api('/api/stage1c/entity?entity_id='+encodeURIComponent(id)+'&build_id='+encodeURIComponent(profile.build_id));if(seq!==detailSeq)return;const e=d.entity;panel.replaceChildren(node('h3',e.display_title),node('p','Source address: '+(e.address||'Not provided')));panel.append(node('p',`Source representative coordinate: ${e.latitude.toFixed(6)}, ${e.longitude.toFixed(6)}. It is not automatically used as a route endpoint.`,'small'));let query=e.display_title;for(const old of e.legacy_reviews){panel.append(node('p',old.note_zh,'warning'));if(old.replacement?.address)query=old.replacement.address;else if(old.replacement?.name)query=old.replacement.name;}if(!e.default_exploration_visible)panel.append(node('p','This is a former location or a source record awaiting review. Finding a newer location does not verify that it is open.','warning'));const actions=node('div',undefined,'detail-actions');const searchButton=node('button','Find a destination by name or current address');searchButton.onclick=()=>{$('destination-query').value=query;resetSearch('destination');$('destination-query').scrollIntoView({behavior:'smooth',block:'center'});search('destination');};const manual=node('button','Select an access point on the map','quiet');manual.onclick=()=>{setPick('destination');if(map){map.stop();map.setView([e.latitude,e.longitude],17,{animate:false});}};const a=node('a','View the full review record ↗');a.href='/map-1c';a.target='_blank';a.rel='noopener';actions.append(searchButton,manual,a);panel.append(actions);if(fly&&map){map.stop();map.setView([e.latitude,e.longitude],15,{animate:false});if(focusLayer)map.removeLayer(focusLayer);focusLayer=L.circleMarker([e.latitude,e.longitude],{pane:'pmPOI',radius:7,color:'#997123',weight:2,fillOpacity:.4,interactive:false}).bindTooltip(node('span',e.display_title)).addTo(map);}}catch(e){if(seq===detailSeq)panel.replaceChildren(node('p',errText(e),'error'));}}
async function loadCatalogue(){try{profile=await api('/api/stage1c/status');let offset=0,total=null,items=[];do{const d=await api('/api/stage1c/features?view=all&limit=1000&offset='+offset+'&build_id='+encodeURIComponent(profile.build_id));if(total===null)total=d.matched_total;if(d.matched_total!==total)throw Error('Catalogue version changed. Refresh the page.');items.push(...d.features);if(d.next_offset!==null&&d.next_offset<=offset)throw Error('Pagination did not advance');offset=d.next_offset;}while(offset!==null);if(items.length!==total)throw Error('The catalogue did not load completely');all=items;renderCatalogue();}catch(e){$('list-count').textContent=errText(e);}}
function loadScript(url,integrity){return new Promise((resolve,reject)=>{const s=node('script');s.src=url;let timer;const fail=()=>{clearTimeout(timer);s.remove();reject(Error('Map component failed to load or timed out'));};if(integrity){s.integrity=integrity;s.crossOrigin='anonymous';}s.onload=()=>{clearTimeout(timer);resolve();};s.onerror=fail;timer=setTimeout(fail,7000);document.head.append(s);});}
async function initMap(){
  const css=node('link');css.rel='stylesheet';css.href='/stage1-static/vendor/leaflet.css';
  css.onerror=()=>{css.onerror=null;css.href='https://unpkg.com/leaflet@1.9.4/dist/leaflet.css';css.integrity='sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=';css.crossOrigin='anonymous';};document.head.append(css);
  try{
    try{await loadScript('/stage1-static/vendor/leaflet.js');}catch{await loadScript('https://unpkg.com/leaflet@1.9.4/dist/leaflet.js','sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=');}
    if(!window.L)throw Error('Leaflet is unavailable');
    $('map').replaceChildren();map=L.map('map',{preferCanvas:false,minZoom:10,maxZoom:20,zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false,wheelDebounceTime:40}).setView([1.3521,103.8198],11);
    for(const [name,z] of [['pmPOI',410],['pmRoute',450],['pmEndpoints',600]]){map.createPane(name);map.getPane(name).style.zIndex=String(z);}
    // Non-interactive top overlays must never capture clicks meant for POIs.
    map.getPane('pmRoute').style.pointerEvents='none';
    map.getPane('pmEndpoints').style.pointerEvents='none';
    routeRenderer=L.svg({pane:'pmRoute',padding:.2}).addTo(map);
    map.on('movestart',()=>{viewRevision++;});
    L.control.scale({imperial:false}).addTo(map);
    basemapControl=PlayMapMapFix.createBasemapController(L,map,info=>{
      if(basemapFrame!==null)cancelAnimationFrame(basemapFrame);
      basemapFrame=requestAnimationFrame(()=>{$('basemap-status').textContent=info.message;$('basemap-status').dataset.level=info.level;basemapFrame=null;});
    });
    basemapControl.set($('basemap').value,$('tiles').checked);
    $('basemap').onchange=()=>basemapControl.set($('basemap').value,$('tiles').checked);
    $('tiles').onchange=()=>basemapControl.set($('basemap').value,$('tiles').checked);
    map.on('click',e=>{if(pickTarget)choose(pickTarget,{latitude:e.latlng.lat,longitude:e.latlng.lng,label:'Selected map point',source:'user_map'});});
    if(window.ResizeObserver){
      let previousWidth=-1,previousHeight=-1;
      new ResizeObserver(entries=>{
        const rect=entries[0]?.contentRect;if(!rect||rect.width<=0||rect.height<=0)return;
        const width=Math.round(rect.width),height=Math.round(rect.height);
        if(width===previousWidth&&height===previousHeight)return;
        previousWidth=width;previousHeight=height;
        if(resizeFrame!==null)cancelAnimationFrame(resizeFrame);
        resizeFrame=requestAnimationFrame(()=>{resizeFrame=null;if(map)map.invalidateSize({animate:false,pan:true,debounceMoveend:true});});
      }).observe($('map'));
    }
    renderCatalogue();refreshEndpoints();
    // A route can finish before Leaflet loads. Render it only if still current.
    if(state.lastSuccess&&state.lastSuccess.revision===state.revision)drawRoute(state.lastSuccess);
  }catch(e){
    const ph=$('map-placeholder');if(ph)ph.textContent='The map component did not load. Run scripts/prepare_stage1_map.py and refresh. Search, coordinate entry and numeric travel results remain available.';
    $('map-notice').textContent=errText(e);$('basemap-status').textContent='Map component is not ready. No basemap has been requested.';
  }
}
function wire(){for(const k of ['origin','destination']){$(k+'-form').onsubmit=e=>{e.preventDefault();search(k);};$(k+'-query').oninput=()=>resetSearch(k);$(k+'-more').onclick=()=>{if(searches[k].next)search(k,searches[k].next);};$('pick-'+k).onclick=()=>{if(!map){$('map-notice').textContent='Map is not ready. Search for a place or enter coordinates instead.';return;}setPick(k);};}
$('coordinate-form').onsubmit=e=>{e.preventDefault();const lat=Number($('manual-lat').value),lon=Number($('manual-lon').value);if(!Number.isFinite(lat)||!Number.isFinite(lon)||lat<.9||lat>1.7||lon<103.4||lon>104.7){$('route-status').textContent='Check the latitude/longitude order and Singapore service area.';$('route-status').className='error';return;}choose($('coordinate-target').value,{latitude:lat,longitude:lon,label:'Entered coordinates',source:'user_coordinates'});};
$('swap').onclick=()=>{state.swap();refreshEndpoints();invalidate();};$('clear').onclick=()=>{state.clear();invalidate();refreshEndpoints();if(routeLayer&&map)map.removeLayer(routeLayer);if(snappedLayer&&map)map.removeLayer(snappedLayer);routeLayer=null;snappedLayer=null;$('route-metrics').hidden=true;$('route-warnings').replaceChildren();$('route-status').textContent='Selections and route cleared from this page.';$('route-version').textContent='Not calculated';setPick(null);$('route-source').textContent='';$('route-diagnostic').hidden=true;$('route-diagnostic').replaceChildren();$('export-route').disabled=true;$('export-status').textContent='';updateFitButtons();};
$('mode').onchange=()=>{state.change('mode',$('mode').value);invalidate();};$('route-button').onclick=calculate;$('refresh-status').onclick=status;
$('catalogue-query').oninput=()=>{clearTimeout(catalogueTimer);catalogueTimer=setTimeout(()=>{listLimit=50;renderCatalogue();},150);};$('category').onchange=$('include-holds').onchange=()=>{clearTimeout(catalogueTimer);listLimit=50;renderCatalogue();};$('catalogue-more').onclick=()=>{listLimit+=50;renderCatalogue();};$('fit').onclick=()=>{if(map){map.stop();map.setView([1.3521,103.8198],11,{animate:false});}};$('auto-route').onchange=()=>{if(!$('auto-route').checked)clearTimeout(autoTimer);};}

function setPick(target){
  pickTarget=target;$('cancel-pick').hidden=!target;
  $('pick-status').textContent=target?'Click the map to select '+(target==='origin'?'A · Start':'B · Destination')+'; press Esc to cancel':'Map selection off';
  $('map').classList.toggle('map-picking',!!target);
}
function updateFitButtons(){
  $('fit-endpoints').disabled=!state.origin||!state.destination;
  $('fit-route').disabled=!routeLayer;
}
function fitRoute(route=state.lastSuccess){
  if(!map||!routeLayer)return;
  const bounds=routeLayer.getBounds();
  // Include selected coordinates too; do not hide a provider snapping gap.
  const r=route;
  if(r){bounds.extend([r.origin.latitude,r.origin.longitude]);bounds.extend([r.destination.latitude,r.destination.longitude]);}
  map.stop();map.fitBounds(bounds,{padding:[35,35],maxZoom:17,animate:false});
}
function drawRoute(r,{fit=true}={}){
  if(!map)return;
  const points=r?.geometry?.coordinates;
  if(r?.geometry?.type!=='LineString'||!Array.isArray(points)||points.length<2||points.some(p=>!Array.isArray(p)||p.length<2||!Number.isFinite(p[0])||!Number.isFinite(p[1])))throw Error('Invalid route geometry. The previous route is retained.');
  let nextRoute=null,nextSnapped=null;
  try{
    nextRoute=L.geoJSON(r.geometry,{pane:'pmRoute',renderer:routeRenderer,style:{pane:'pmRoute',renderer:routeRenderer,color:'#175db5',weight:5,opacity:.95,dashArray:null},interactive:false}).addTo(map);
    nextSnapped=L.layerGroup().addTo(map);
    for(const p of [points[0],points[points.length-1]])L.circleMarker([p[1],p[0]],{pane:'pmRoute',renderer:routeRenderer,radius:3,color:'#175db5',fillColor:'#fff',fillOpacity:1,interactive:false}).addTo(nextSnapped);
  }catch(e){
    if(nextRoute&&map.hasLayer(nextRoute))map.removeLayer(nextRoute);
    if(nextSnapped&&map.hasLayer(nextSnapped))map.removeLayer(nextSnapped);
    throw Error('Route data returned, but drawing failed. The previous route is retained.'+errText(e));
  }
  // Commit the new display only after its geometry was successfully constructed.
  if(routeLayer)map.removeLayer(routeLayer);if(snappedLayer)map.removeLayer(snappedLayer);
  routeLayer=nextRoute;snappedLayer=nextSnapped;
  if(fit)fitRoute(r);updateFitButtons();
}

function exportRoute(){
  const r=state.lastSuccess;if(!r||r.revision!==state.revision){$('export-status').textContent='Calculate a route for the current inputs first.';return;}
  const exact=$('export-coordinates').checked;
  if(exact&&!window.confirm('This diagnostic file will include the selected coordinates and full route, which may reveal your location. Export it?'))return;
  const report=PlayMapMapFix.makeExport(r,{exactCoordinates:exact,currentRevision:state.revision,basemap:$('basemap').value});
  const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));
  const link=node('a');link.href=url;link.download='playmap_route_diagnostic_'+new Date().toISOString().replace(/[:.]/g,'-')+'.json';
  document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
  $('export-status').textContent='Local file created. Nothing was uploaded.'+(exact?'Includes selected coordinates and the route. Review before sharing.':'Excludes exact coordinates, place names and tokens.');
}
function wireMapFix(){
  $('show-pois').onchange=renderCatalogue;$('cancel-pick').onclick=()=>setPick(null);
  document.addEventListener('keydown',e=>{if(e.key==='Escape')setPick(null);});
  $('fit-route').onclick=()=>fitRoute();
  $('fit-endpoints').onclick=()=>{if(map&&state.origin&&state.destination){map.stop();map.fitBounds([[state.origin.latitude,state.origin.longitude],[state.destination.latitude,state.destination.longitude]],{padding:[45,45],maxZoom:17,animate:false});}};
  $('export-route').onclick=exportRoute;
}

let lastViewReport=null;
$('check-view').onclick=()=>{
  lastViewReport=PlayMapViewCheck.inspect({L:window.L,map,state,endpointMarkers,routeLayer,poiLayer,basemap:$('basemap').value});
  const panel=$('view-check-output');panel.replaceChildren();
  for(const c of lastViewReport.checks)panel.append(node('p',c.status.toUpperCase()+' · '+c.detail));
  $('export-view').disabled=false;
};
$('export-view').onclick=()=>{
  if(!lastViewReport)return;
  const url=URL.createObjectURL(new Blob([JSON.stringify(lastViewReport,null,2)],{type:'application/json'}));
  const a=node('a');a.href=url;a.download='playmap_view_diagnostic.json';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
};

wire();wireMapFix();status();loadCatalogue();initMap();
