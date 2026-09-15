/* 2A-M2. Display diagnostics only: never selects, shortens, or repairs a route. */
(function (root) {
  'use strict';
  const VERSION = '2A-M2';
  const attribution = '<a href="https://www.onemap.gov.sg/" target="_blank" rel="noopener"><img src="https://www.onemap.gov.sg/web-assets/images/logo/om_logo_256.png" alt="OneMap">OneMap</a> | Map data &copy; contributors, <a href="https://www.sla.gov.sg/" target="_blank" rel="noopener">Singapore Land Authority</a>';
  // Official OneMap TileJSON: native zoom 11..19, lon/lat bounds below.
  // Layer bounds limit tile requests, NEVER place records or selected coordinates.
  const BASEMAPS = Object.freeze({
    onemap: {label:'OneMap 标准',url:'https://www.onemap.gov.sg/maps/tiles/Default_HD/{z}/{x}/{y}.png',
      attribution,minNativeZoom:11,maxNativeZoom:19,bounds:[[1.16,103.502],[1.56073,104.11475]]},
    'onemap-grey': {label:'OneMap 灰色',url:'https://www.onemap.gov.sg/maps/tiles/Grey_HD/{z}/{x}/{y}.png',
      attribution,minNativeZoom:11,maxNativeZoom:19,bounds:[[1.16,103.502],[1.56073,104.11475]]},
    // OSM covers the world, this project does not. Bounds mirror the backend
    // SG_REQUEST_ENVELOPE so the drawn map never extends past where a selected
    // point would be accepted; a worldwide basemap would imply coverage that
    // every click outside Singapore then contradicts.
    osm: {label:'OpenStreetMap',url:'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors',
      minNativeZoom:0,maxNativeZoom:19,bounds:[[0.9,103.4],[1.7,104.7]]}
  });
  const numeric = value => typeof value === 'number' && Number.isFinite(value);
  const rounded = value => numeric(value) ? Math.round(value * 100) / 100 : null;
  function haversine(a,b) {
    const rad=Math.PI/180, lat1=a[1]*rad, lat2=b[1]*rad;
    const h=Math.sin((lat2-lat1)/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin((b[0]-a[0])*rad/2)**2;
    return 6371008.8*2*Math.atan2(Math.sqrt(Math.max(0,Math.min(1,h))),Math.sqrt(Math.max(0,1-h)));
  }
  function diagnostics(route) {
    const a=[route.origin.longitude,route.origin.latitude],b=[route.destination.longitude,route.destination.latitude];
    const direct=haversine(a,b), points=route.geometry.coordinates;
    let length=0;
    for(let i=1;i<points.length;i++)length+=haversine(points[i-1],points[i]);
    const ratio=direct>=1?route.distance_m/direct:null;
    const gap=Math.abs(length-route.distance_m), rel=gap/Math.max(length,route.distance_m,1);
    return {straight_line_m:rounded(direct),geometry_length_m:rounded(length),
      provider_distance_m:rounded(route.distance_m),distance_to_straight_ratio:rounded(ratio),
      geometry_summary_difference_m:rounded(gap),
      large_detour_flag:direct>=300&&route.distance_m>=3000&&ratio>=4,
      geometry_summary_mismatch:gap>300&&rel>.35,
      thresholds:{min_direct_m:300,min_route_m:3000,ratio:4,geometry_absolute_m:300,geometry_relative:.35},
      thresholds_are_unvalidated_display_heuristics:true,shortest_route_proven:false,
      interpretation:'Straight-line distance is only a reference, not a traversable route or alternative travel time.'};
  }
  function makeExport(route,options={}) {
    const exact=options.exactCoordinates===true;
    // Strict allowlist: never serialize state, request headers, free text, or config.
    const out={schema_version:1,ui_patch:VERSION,exported_at:new Date().toISOString(),
      provider:'OneMap',requested_route_type:['walk','drive','cycle'].includes(route.mode)?route.mode:null,
      mode_is_request_parameter_not_independent_provider_confirmation:true,
      route_revision:route.revision,current_revision:options.currentRevision,
      route_matches_current_revision:route.revision===options.currentRevision,
      provider_distance_m:rounded(route.distance_m),provider_duration_s:rounded(route.duration_s),
      endpoint_offsets_m:{origin:rounded(route.endpoint_offsets_m?.origin),destination:rounded(route.endpoint_offsets_m?.destination)},
      geometry_point_count:route.geometry.coordinates.length,diagnostics:diagnostics(route),
      exact_coordinates_included:exact,credentials_included:false,labels_included:false,
      uploaded_automatically:false,route_algorithm_changed:false};
    if(exact){
      const point=p=>({latitude:p.latitude,longitude:p.longitude});
      out.origin=point(route.origin);out.destination=point(route.destination);
      out.geometry={type:'LineString',coordinates:route.geometry.coordinates.map(p=>[p[0],p[1]])};
    }
    if(options.basemap&&BASEMAPS[options.basemap])out.basemap=options.basemap;
    return out;
  }
  function createBasemapController(L,map,onStatus) {
    let layer=null,serial=0,timer=null;
    let info={provider:'onemap',enabled:true,paused:false,requested:0,loaded:0,failed:0};
    const emit=(level,message)=>onStatus({...info,level,message});
    const detach=()=>{
      clearTimeout(timer);timer=null;
      const old=layer;layer=null;
      if(!old)return;
      // IMPORTANT: Leaflet installs its OWN 'remove' listeners in Layer._layerAdd
      // and Attribution._addAttribution. Calling off() first deletes these hooks,
      // leaving dead GridLayer listeners on map zoom/move and stale attribution.
      // Let removeLayer fire the lifecycle event, THEN release remaining handlers.
      if(map.hasLayer(old))map.removeLayer(old);
      old.off();
    };
    function pause(id,message) {
      if(id!==serial||info.paused)return;info.paused=true;detach();emit('error',message);
    }
    function set(provider=info.provider,enabled=info.enabled) {
      if(!BASEMAPS[provider])throw Error('Unsupported basemap');
      const id=++serial;detach();info={provider,enabled:!!enabled,paused:false,requested:0,loaded:0,failed:0};
      if(!enabled){emit('off','在线底图已关闭；选点和路线保留。');return;}
      const cfg=BASEMAPS[provider];
      layer=L.tileLayer(cfg.url,{attribution:cfg.attribution,minZoom:10,maxZoom:20,
        minNativeZoom:cfg.minNativeZoom,maxNativeZoom:cfg.maxNativeZoom,bounds:cfg.bounds,
        tileSize:256,detectRetina:false,noWrap:true,updateWhenIdle:true,updateWhenZooming:false,keepBuffer:1,
        referrerPolicy:'strict-origin-when-cross-origin'});
      // No proxy, no identity spoofing, no tile cache-busting, no automatic retry.
      layer.on('tileloadstart',()=>{if(id===serial)info.requested++;});
      layer.on('tileload',e=>{
        if(id!==serial||info.paused||e.tile?.dataset?.playmapFailed)return;
        info.loaded++;clearTimeout(timer);
        emit(info.failed?'warning':'ok',cfg.label+' · 已载入 '+info.loaded+' 块底图'+(info.failed?'；部分失败，可切换底图或关闭。':''));
      });
      layer.on('tileerror',e=>{
        if(id!==serial||info.paused)return;info.failed++;
        // Hide HTTP/image failures, but do not claim to detect error images sent as HTTP 200.
        if(e.tile){e.tile.style.visibility='hidden';if(e.tile.dataset)e.tile.dataset.playmapFailed='1';}
        emit('warning',cfg.label+' · 底图请求失败；不影响已取得的路径。可切换底图或关闭。');
        if(info.failed>=3)queueMicrotask(()=>pause(id,cfg.label+' · 多次加载失败，已暂停底图请求。请切换底图，或关闭后手动开启；没有删除路线。'));
      });
      emit('loading',cfg.label+' · 正在加载当前视野底图…');
      layer.addTo(map);
      timer=setTimeout(()=>{if(id===serial&&!info.loaded)pause(id,cfg.label+' · 底图加载超时或当前视野不在覆盖范围，已暂停；地点与路线仍保留。');},15000);
    }
    return {set,getState:()=>({...info}),destroy:()=>{serial++;detach();}};
  }
  const exported={VERSION,BASEMAPS,haversine,diagnostics,makeExport,createBasemapController};
  root.PlayMapMapFix=exported;
  if(typeof module!=='undefined'&&module.exports)module.exports=exported;
})(typeof window==='undefined'?globalThis:window);
