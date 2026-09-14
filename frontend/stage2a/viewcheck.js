/* Display-only checks. No credentials, labels, lat/lon, or provider requests. */
(function(root){
 'use strict';
 function intersects(a,b){return a.right>b.left&&a.left<b.right&&a.bottom>b.top&&a.top<b.bottom;}
 function inspect({L,map,state,endpointMarkers,routeLayer,poiLayer,basemap}){
  const out={schema_version:1,ui_patch:'2A-M2',checked_at:new Date().toISOString(),
   exact_coordinates_included:false,labels_included:false,credentials_included:false,
   uploaded_automatically:false,provider_requests_made:false,checks:[]};
  const add=(name,status,detail)=>out.checks.push({name,status,detail});
  if(!map){add('map_ready','fail','地图组件尚未就绪');return out;}
  const rect=map.getContainer().getBoundingClientRect(),size=map.getSize();
  out.view={width_px:Math.round(rect.width),height_px:Math.round(rect.height),zoom:map.getZoom(),basemap,
   canvas_count:map.getContainer().querySelectorAll('canvas').length,
   selected_endpoint_count:Number(!!state.origin)+Number(!!state.destination),
   current_revision:state.revision,route_revision:state.lastSuccess?.revision??null};
  add('container_size',Math.abs(size.x-rect.width)<=2&&Math.abs(size.y-rect.height)<=2?'pass':'fail','地图内部尺寸与页面尺寸比较（像素）');
  let tiles=0;map.eachLayer(l=>{if(l instanceof L.TileLayer)tiles++;});
  out.view.active_tile_layers=tiles;add('tile_layer_count',tiles<=1?'pass':'fail','同时启用的底图不应超过一个');
  for(const target of ['origin','destination']){
   const p=state[target],m=endpointMarkers[target];
   if(!p){add(target+'_marker','skip','未选该点');continue;}
   if(!m||!map.hasLayer(m)||!m.getElement()){add(target+'_marker','fail','缺少已选点标记');continue;}
   const e=m.getElement().getBoundingClientRect(),expected=map.latLngToContainerPoint(m.getLatLng());
   const gap=Math.hypot(e.left+e.width/2-rect.left-expected.x,e.top+e.height/2-rect.top-expected.y);
   add(target+'_alignment',gap<=3?'pass':'fail','标记中心偏移 '+gap.toFixed(1)+' px');
   add(target+'_in_view',intersects(rect,e)?'pass':'info','不在视野时可点“聚焦起终点”');
  }
  if(!routeLayer){add('route_layer','skip','尚无已绘制路线');}
  else {
   let paths=0,inView=0;routeLayer.eachLayer(l=>{const e=l.getElement?.();if(!e)return;paths++;if(intersects(rect,e.getBoundingClientRect())&&getComputedStyle(e).display!=='none')inView++;});
   out.view.route_paths=paths;add('route_svg',paths>0?'pass':'fail','路线使用实际SVG路径元素');
   add('route_in_view',inView>0?'pass':'info','不在视野时可点“查看整条路线”');
  }
  let tested=0,hits=0;
  if(poiLayer&&map.hasLayer(poiLayer))poiLayer.eachLayer(l=>{
   if(tested>=12)return;const el=l.getElement?.();if(!el)return;const r=el.getBoundingClientRect();
   const x=r.left+r.width/2,y=r.top+r.height/2;
   if(x<=rect.left+15||x>=rect.right-15||y<=rect.top+15||y>=rect.bottom-15||x<0||y<0||x>innerWidth||y>innerHeight)return;
   tested++;const hit=document.elementFromPoint(x,y);if(hit===el||el.contains(hit))hits++;
  });
  out.view.tested_visible_points=tested;out.view.native_hit_points=hits;
  add('poi_hit_targets',tested===0?'skip':hits>0?'pass':'warning','当前视野抽查 '+tested+' 个显示点；可直接命中 '+hits+' 个。弹窗/控件可能遮挡部分点。');
  out.has_failed_check=out.checks.some(c=>c.status==='fail');return out;
 }
 root.PlayMapViewCheck={inspect};if(typeof module!=='undefined'&&module.exports)module.exports={inspect};
})(typeof window==='undefined'?globalThis:window);
