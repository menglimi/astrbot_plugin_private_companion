"use strict";
window.CompanionStateMaintenance = (() => {
  const phases={menstrual:"经期",follicular:"卵泡期",pre_ovulation:"排卵前",ovulation:"排卵期",luteal:"黄体期",pms:"经前期"};
  const phaseDescriptions={menstrual:"节奏可以放慢，按实际感受表达",follicular:"精力逐步回升，保持自然变化",pre_ovulation:"逐渐轻盈，不要求突然改变性格",ovulation:"可以更明朗，不自动推断亲密或生育",luteal:"整体趋于平稳，保留个体差异",pms:"可以更想安静，也可以照常相处"};
  let selectedPhase="menstrual";
  let selectedCycleView="plan";
  const modes={basic:"基础三阶段",six_phase:"六阶段"};
  const scheduleModes={continuous:"连续日程（HDSI）",orchestrated:"日程编排"};
  const initialization={natural:"自然定位",unknown:"暂不定位",first_day:"从第 1 天开始"};
  const applicability={applicable:"指定当前周期日",not_applicable:"该角色不适用",unknown:"保持尚未确定",auto:"交回角色判断"};
  const discomfortChoices=Object.fromEntries(["痛经","头痛","腰酸","乏力","情绪低落","恶心"].map(value=>[value,value]));
  const homeFeatures={
    energy:{label:"能量",icon:"battery-medium",help:"模拟日常精力的自然起伏。"},
    health:{label:"健康",icon:"heart-pulse",help:"记录角色自身的主观体感。",special:"仅生病或不适时"},
    hunger:{label:"饥饿",icon:"utensils",help:"模拟随作息变化的饥饿感受。",special:"仅饥饿时"},
    sleep:{label:"睡眠状态",icon:"moon-star",help:"只维护困意与主观睡眠感受，不会按时入睡。自动作息需在「日记与生活」同时开启生活日程总开关和「按角色作息自动入睡」。",special:"仅失眠或睡不好时"},
    cycle:{label:"生理期",icon:"calendar-heart",help:"模拟周期阶段与阶段表达。",special:"仅经期"},
    mood:{label:"心情",icon:"smile",help:"模拟互动中的情绪变化与回归。"},
    busy:{label:"忙碌",icon:"briefcase-business",help:"模拟当下的注意力状态；日程安排独立设置。",special:"仅忙碌时"},
    location:{label:"位置",icon:"map-pin",help:"记录角色自己的模拟位置。"},
  };
  const definitions={
    enabled:{label:"启用后台状态维护",type:"boolean"},
    maintenance_grant_scope:{label:"后台维护授权范围",type:"select",choices:{role:"整个角色（默认）",window:"仅当前私聊窗口"}},
    maintenance_minutes:{label:"维护间隔（分钟）",type:"number",max:1440},
    calendar_enabled:{label:"启用角色日程",type:"boolean"},
    home_show_energy:{label:"能量",type:"boolean"},
    home_show_health:{label:"健康",type:"boolean"},
    home_show_hunger:{label:"饥饿",type:"boolean"},
    home_show_sleep:{label:"睡眠",type:"boolean"},
    home_show_cycle:{label:"生理期",type:"boolean"},
    home_show_busy:{label:"忙碌",type:"boolean"},
    home_show_location:{label:"位置",type:"boolean"},
    home_show_mood:{label:"心情",type:"boolean"},
    ...Object.fromEntries(["energy","sleep","busy","location"].map(name=>[name+"_enabled",{label:"启用"+homeFeatures[name].label+"模拟",type:"boolean"}])),
    ...Object.fromEntries(Object.entries(homeFeatures).filter(([,spec])=>spec.special).map(([name,spec])=>["home_only_special_"+name,{label:spec.label+"展示条件",type:"boolean"}])),
    humanized_state_intensity:{label:"状态表达强度",type:"number",min:0,max:100},
    schedule_simulation_mode:{label:"日程模式",type:"select",choices:scheduleModes},
    planning_enabled:{label:"启用自动规划",type:"boolean"},
    planning_timezone:{label:"规划时区",type:"text"},
    planning_horizon_days:{label:"长期规划范围（天）",type:"number",max:366},
    planning_refine_minutes:{label:"提前细化时间（分钟）",type:"number",max:1440},
    planning_max_items:{label:"每次最多安排（项）",type:"number",max:32},
    planning_prompt:{label:"规划偏好",type:"textarea",maxLength:4096,placeholder:"例如：平日留出阅读时间，周末适当放松；已有约定优先，给临时变化留出余地。"},
    /* 数据结构是 friend 的，不该让人手写。界面上按年份卡填表单，
       这里只在「高级」折叠里作为原始数据出口。placeholder 因此不再是 JSON。*/
    planning_annual_calendars:{label:"年度日历与调休",type:"textarea",maxLength:16384,
      placeholder:"留空即使用默认日历。"},
    planning_weather_enabled:{label:"启用角色天气感知",type:"boolean"},
    planning_weather_location:{label:"角色天气地点",type:"weather-location",maxLength:512},
    planning_weather_max_age_minutes:{label:"天气信息最长沿用（分钟）",type:"number",min:5,max:120},
    hunger_enabled:{label:"模拟饥饿感受",type:"boolean"},
    health_enabled:{label:"模拟健康感受",type:"boolean"},
    energy_expression_prompt:{label:"精力模拟引导",type:"textarea",maxLength:512,
      placeholder:"例如：精力随作息和近期活动缓慢起伏，不必每天变化。"},
    hunger_expression_prompt:{label:"饥饿感受引导",type:"textarea",maxLength:512,
      placeholder:"例如：结合作息与活动自然变化，不把聊天中的吃饭建议当成已经进食。"},
    health_expression_prompt:{label:"健康感受引导",type:"textarea",maxLength:512,
      placeholder:"例如：只表达角色自己的主观体感，不推断疾病或把短暂不适写成诊断。"},
    cycle_enabled:{label:"启用生理期模拟",type:"boolean"},
    cycle_mode:{label:"阶段模式",type:"select",choices:modes},
    cycle_timezone:{label:"周期时区",type:"text"},
    cycle_initialization:{label:"首次定位方式",type:"select",choices:initialization},
    cycle_variation_enabled:{label:"允许阶段天数轻微波动",type:"boolean"},
    cycle_discomfort_enabled:{label:"允许模拟不适感受",type:"boolean"},
    cycle_link_intensity:{label:"精力趋势随状态强度缩放",type:"boolean"},
    cycle_discomfort_chance:{label:"参考概率",type:"range",min:0,max:100},
    cycle_discomfort_types:{label:"可选体感",type:"checklist",choices:discomfortChoices},
    cycle_expression_prompt:{label:"角色表达偏好",type:"textarea"},
    /* 心情动力学的三项主参数。mood_enabled 是总闸，这三项决定它怎么动：
       概率控制“这一轮情绪到底动没动”，步长控制“一次最多动多少”，
       半衰期控制“多久回归基线”。*/
    mood_enabled:{label:"启用心情模拟",type:"boolean"},
    mood_update_probability:{label:"每轮更新心情的概率",type:"probability"},
    mood_update_max_delta:{label:"单轮最大变化幅度",type:"number",min:1,max:4},
    mood_decay_half_life_minutes:{label:"回归基线半衰期（分钟）",type:"number",min:5,max:1440},
    /* 每日巡视：V1 的日检节奏与保留天数，V2 由 humanized.py 消费。*/
    daily_state_patrol_enabled:{label:"启用每日状态巡视",type:"boolean"},
    daily_state_patrol_time:{label:"巡视时刻（HH:MM，本地时间）",type:"time"},
    daily_state_patrol_retention_days:{label:"巡视记录保留天数",type:"number",min:3,max:180},
    typing_enabled:{label:"启用正在输入提示",type:"boolean"},
    typing_refresh_seconds:{label:"输入提示刷新间隔（秒）",type:"number",min:1,max:10},
    presence_enabled:{label:"启用 QQ 自定义短状态",type:"boolean"},
    ...Object.fromEntries(Object.entries(phases).map(([key,label])=>["cycle_"+key+"_days",{label:label+"天数",type:"number",max:30}])),
    /* 六阶段的能量与提示词。能量是 -50–30 的偏移量（负值表示该阶段精力偏低），
       提示词是角色在该阶段实际读到的表达引导，上限 512 字。
       六个 _mood 氛围词刻意不接控件：默认「疲惫/轻快/敏感」这类词已够用，
       接上只是多六个低价值输入框。但它们仍要声明**正确的上限 80**——
       闭包检查会读它们，页面若按默认 2048 放行，值到后端才被拒，
       整个周期设置会连带存不进去。*/
    ...Object.fromEntries(Object.entries(phases).flatMap(([key,label])=>[
      ["cycle_"+key+"_energy",{label:label+"精力偏移",type:"number",min:-50,max:30}],
      ["cycle_"+key+"_prompt",{label:label+"表达引导",type:"textarea",maxLength:512,
        placeholder:"例如：处于"+label+"，身体更容易疲倦，情绪感受稍敏锐。"}],
      ["cycle_"+key+"_mood",{label:label+"氛围词",type:"text",maxLength:80}]])),
  };
  /* cycle 的前十二位顺序被 form() 的 slice(0,6)/slice(6,12) 依赖：
     前六项是开关与模式，接着六项是阶段天数。新字段一律追加在第十二位之后，
     否则「阶段时长」那一块会串到别的字段上。 */
  const groups={home:["enabled","maintenance_grant_scope","maintenance_minutes",...Object.keys(homeFeatures).flatMap(name=>[
      name+"_enabled","home_show_"+name,...(homeFeatures[name].special?["home_only_special_"+name]:[])])],
    intensity:["humanized_state_intensity","energy_expression_prompt"],
    maintenance:["hunger_enabled","health_enabled"],
    health:["health_enabled","health_expression_prompt"],
    hunger:["hunger_enabled","hunger_expression_prompt"],
    weather:["planning_weather_enabled","planning_weather_location","planning_weather_max_age_minutes"],
    datetime:["planning_timezone"],
    planning:["calendar_enabled","schedule_simulation_mode","planning_enabled","planning_timezone","planning_horizon_days","planning_refine_minutes","planning_max_items","planning_annual_calendars","planning_prompt"],
    cycle:["cycle_enabled","cycle_mode","cycle_timezone","cycle_initialization","cycle_variation_enabled","cycle_discomfort_enabled",
      ...Object.keys(phases).map(key=>"cycle_"+key+"_days"),"cycle_expression_prompt","cycle_link_intensity","cycle_discomfort_chance","cycle_discomfort_types",
      ...Object.keys(phases).flatMap(key=>["cycle_"+key+"_energy","cycle_"+key+"_prompt","cycle_"+key+"_mood"])],
    mood:["mood_enabled","mood_update_probability","mood_update_max_delta","mood_decay_half_life_minutes"],
    patrol:["daily_state_patrol_enabled","daily_state_patrol_time","daily_state_patrol_retention_days"]};
  const fields=kind=>groups[kind]||[];
  const isTimezone=name=>name==="cycle_timezone"||name==="planning_timezone";
  const label=name=>definitions[name]?.label||name;
  const length=settings=>Object.keys(phases).reduce((total,key)=>total+Number(settings["cycle_"+key+"_days"]),0);
  /* 数值字段的下限不能写死成 1：阶段能量是 -50–30，负值表示该阶段精力偏低。
     没声明 min 的沿用旧行为（1），声明了的按声明来。 */
  const minimumOf=field=>field.min??1;
  const rangeText=field=>field.min!=null&&field.min<0?`${field.min}–${field.max} 之间`:`${minimumOf(field)}–${field.max} 之间`;
  /* 后端的 parse_patrol_time 要求恰好 HH:MM、无秒、无时区后缀。
     type="time" 的控件值也是这个形状，但手工输入或旧快照可能带秒，所以仍要判。 */
  const isClock=value=>/^([01]\d|2[0-3]):[0-5]\d$/.test(value);
  const WEATHER_LOCATION_PARTS=["name","latitude","longitude","alert_city","alert_area"];
  function weatherLocationParts(value) {
    let row={};
    try {row=JSON.parse(value||"{}");} catch {}
    if(!row||typeof row!=="object"||Array.isArray(row))row={};
    return {name:typeof row.name==="string"?row.name:"",
      latitude:row.latitude==null?"":String(row.latitude),
      longitude:row.longitude==null?"":String(row.longitude),
      alert_city:typeof row.alert_city==="string"?row.alert_city:"",
      alert_area:typeof row.alert_area==="string"?row.alert_area:""};
  }
  function weatherLocationValue(parts) {
    const value=Object.fromEntries(WEATHER_LOCATION_PARTS.map(name=>[name,String(parts?.[name]??"").trim()]));
    if(!WEATHER_LOCATION_PARTS.some(name=>value[name]))return "";
    if(!value.name||[...value.name].length>64)throw new Error("请填写不超过 64 字的角色所在城市或区域。");
    if([value.name,value.alert_city,value.alert_area].some(text=>/[\u0000-\u001f]/.test(text)))
      throw new Error("地点名称不能包含控制字符。");
    if(!!value.latitude!==!!value.longitude)throw new Error("纬度和经度需要成对填写，或全部留空由系统解析。");
    if(value.latitude||value.longitude) {
      const latitude=Number(value.latitude),longitude=Number(value.longitude);
      if(!Number.isFinite(latitude)||latitude < -90||latitude > 90||!Number.isFinite(longitude)||longitude < -180||longitude > 180)
        throw new Error("纬度范围为 −90 至 90，经度范围为 −180 至 180。");
      value.latitude=latitude;value.longitude=longitude;
    } else {delete value.latitude;delete value.longitude;}
    if(!!value.alert_city!==!!value.alert_area)throw new Error("官方预警的城市与区域请同时填写，或同时留空。");
    if([...value.alert_city].length>64||[...value.alert_area].length>64)throw new Error("预警城市和区域最多各 64 字。");
    if(!value.alert_city){delete value.alert_city;delete value.alert_area;}
    const result={name:value.name};
    for(const name of ["latitude","longitude","alert_city","alert_area"])
      if(Object.hasOwn(value,name))result[name]=value[name];
    const encoded=JSON.stringify(result);
    if(new TextEncoder().encode(encoded).length>512)throw new Error("角色天气地点配置不能超过 512 字节。");
    return encoded;
  }
  function validWeatherLocation(value) {
    if(typeof value!=="string"||new TextEncoder().encode(value).length>512)return false;
    if(!value.trim())return true;
    try {
      const row=JSON.parse(value);
      if(!row||typeof row!=="object"||Array.isArray(row)||Object.keys(row).some(key=>!WEATHER_LOCATION_PARTS.includes(key)))return false;
      if((Object.hasOwn(row,"latitude")&&typeof row.latitude!=="number")
          ||(Object.hasOwn(row,"longitude")&&typeof row.longitude!=="number")
          ||["name","alert_city","alert_area"].some(key=>Object.hasOwn(row,key)&&typeof row[key]!=="string"))return false;
      return !!weatherLocationValue(weatherLocationParts(value));
    } catch {return false;}
  }
  /* 年度日历：字段本身是 JSON 字符串，但不该让用户手写 JSON。
     界面上按「年份卡 → 节假日 / 调休上班 / 节气」填表单，
     结构化编辑即时序列化回 planning_annual_calendars；
     「高级」折叠里的原始文本框读写同一个字段，两边不会各说各话。
     校验规则与后端 companion_v2/agenda/annual_calendar.py 对齐，
     在保存前就把问题说清楚，而不是等宿主拒收再猜。*/
  const ANNUAL_SCHEMA="companion.annual-calendar.v1";
  const ANNUAL_YEARS_MAX=3;
  const ANNUAL_DAYS_MAX=96;
  const ANNUAL_NAME_MAX=64;
  const ANNUAL_BYTES_MAX=16384;
  const ANNUAL_MONTHS=["一","二","三","四","五","六","七","八","九","十","十一","十二"];
  /* 分节定义驱动三段的表头、行内控件与「添加」按钮文案，改一处即可。*/
  const ANNUAL_SECTIONS=[
    {key:"holidays",title:"节假日",add:"添加节假日",
      help:"填名称与起止日期，可跨天；同一天只能属于一个节假日。",
      fields:[{name:"name",type:"text",label:"名称",placeholder:"如 国庆节"},
        {name:"start",type:"date",label:"开始日期"},
        {name:"end",type:"date",label:"结束日期"}]},
    {key:"makeup_workdays",title:"调休上班",add:"添加调休日",
      help:"被调成工作日的周末；不能与节假日落在同一天。",
      fields:[{name:"date",type:"date",label:"日期"},
        {name:"name",type:"text",label:"名称",placeholder:"留空记作 调休上班"}]},
    {key:"solar_terms",title:"节气",add:"添加节气",
      help:"补充节令名称，供角色自然感知时令。",
      fields:[{name:"date",type:"date",label:"日期"},
        {name:"name",type:"text",label:"名称",placeholder:"如 立春"}]}];
  const annualText=value=>String(value??"").trim();
  /* 宽松解析：认得出结构就返回 {calendars}，认不出返回 null。
     null 时不渲染结构化卡（只留原始文本框），避免把看不懂的内容悄悄改写掉。*/
  function annualCalendars(value) {
    const text=annualText(value);
    if(!text)return {calendars:[]};
    let rows;
    try {rows=JSON.parse(text);} catch {return null;}
    if(!Array.isArray(rows))return null;
    const calendars=[];
    for(const row of rows) {
      if(!row||typeof row!=="object"||Array.isArray(row)||!Number.isInteger(row.year))return null;
      const read=(key,names)=>Array.isArray(row[key])
        ?row[key].filter(item=>item&&typeof item==="object"&&!Array.isArray(item))
          .map(item=>Object.fromEntries(names.map(name=>[name,String(item[name]??"")])))
        :[];
      calendars.push({year:row.year,
        holidays:read("holidays",["name","start","end"]),
        makeup_workdays:read("makeup_workdays",["date","name"]),
        solar_terms:read("solar_terms",["date","name"])});
    }
    return {calendars};
  }
  /* 只认「完整日期」：2026-2-3、2026-02-30 这类都要挡掉，
     因为后端 _day() 要求 date.fromisoformat 与原文完全一致。*/
  function calendarDay(text) {
    if(typeof text!=="string"||!/^\d{4}-\d{2}-\d{2}$/.test(text))return null;
    const [year,month,day]=text.split("-").map(Number),at=Date.UTC(year,month-1,day),value=new Date(at);
    return value.getUTCFullYear()===year&&value.getUTCMonth()===month-1&&value.getUTCDate()===day
      ?{year,month,day,at}:null;
  }
  /* 序列化：编辑时 keepEmpty=true，保留刚添加还没填的行（重绘后这一行必须还在）；
     落库时 keepEmpty=false，把空行清掉，不写进后端。*/
  function annualCalendarsValue(calendars, keepEmpty=true) {
    const rows=(calendars||[]).map(calendar=>{
      const year=Number(calendar.year);
      const row={schema:ANNUAL_SCHEMA,year:Number.isInteger(year)?year:0,
        holidays:[],makeup_workdays:[],solar_terms:[]};
      for(const item of calendar.holidays||[]) {
        const name=String(item.name??"").trim(),start=String(item.start??"").trim(),end=String(item.end??"").trim();
        if(!keepEmpty&&!name&&!start&&!end)continue;
        row.holidays.push({name,start,end});
      }
      for(const item of calendar.makeup_workdays||[]) {
        const date=String(item.date??"").trim(),name=String(item.name??"").trim();
        if(!keepEmpty&&!date&&!name)continue;
        row.makeup_workdays.push({date,name:name||"调休上班"});
      }
      for(const item of calendar.solar_terms||[]) {
        const date=String(item.date??"").trim(),name=String(item.name??"").trim();
        if(!keepEmpty&&!date&&!name)continue;
        row.solar_terms.push({date,name});
      }
      return row;
    });
    return rows.length?JSON.stringify(rows):"";
  }
  function annualCounts(calendar) {
    let holidays=0;
    for(const item of calendar.holidays) {
      const start=calendarDay(item.start),end=calendarDay(item.end);
      if(start&&end&&end.at>=start.at)holidays+=Math.round((end.at-start.at)/86400000)+1;
    }
    return {holidays,
      makeup:calendar.makeup_workdays.filter(item=>calendarDay(item.date)).length,
      terms:calendar.solar_terms.filter(item=>calendarDay(item.date)&&item.name.trim()).length};
  }
  /* 各月已标记天数：让「填进去的东西到底落在哪」一眼可见，
     否则这张卡填完还是一串日期，用户无从复核。*/
  function annualMarks(calendar) {
    const year=Number(calendar.year),marks=new Array(12).fill(0);
    const add=text=>{const day=calendarDay(text);if(day&&day.year===year)marks[day.month-1]+=1;};
    for(const item of calendar.holidays) {
      const start=calendarDay(item.start),end=calendarDay(item.end);
      if(!start||!end||end.at<start.at)continue;
      for(let at=start.at;at<=end.at;at+=86400000)add(new Date(at).toISOString().slice(0,10));
    }
    for(const item of calendar.makeup_workdays)add(item.date);
    for(const item of calendar.solar_terms)add(item.date);
    return marks;
  }
  /* 保存前把问题说清楚。返回空串表示可用；这也是 normalized() 的报错来源。*/
  function annualCalendarsProblem(value) {
    const text=annualText(value);
    if(!text)return "";
    if(new TextEncoder().encode(text).length>ANNUAL_BYTES_MAX)
      return "年度日历数据超过 16 KB，请减少年份或条目。";
    const model=annualCalendars(text);
    if(!model)return "年度日历的数据无法解析，请用「高级 · 原始数据」修正，或清空后改用上面的表单填写。";
    if(!model.calendars.length)return "";
    if(model.calendars.length>ANNUAL_YEARS_MAX)
      return `最多配置 ${ANNUAL_YEARS_MAX} 个年份，当前是 ${model.calendars.length} 个。`;
    const years=new Set();
    for(const calendar of model.calendars) {
      const year=Number(calendar.year);
      if(!Number.isInteger(year)||year<1||year>9999)return "每个年份都要填 1–9999 之间的整数。";
      if(years.has(year))return `${year} 年填了两份；同一年份只保留一份就够。`;
      years.add(year);
      const days=new Set(),holidayNames=new Map();
      for(const item of calendar.holidays) {
        if(!item.name.trim()&&!item.start.trim()&&!item.end.trim())continue; /* 刚添加还没填的行不报错 */
        const name=item.name.trim();
        if(!name)return `${year} 年有一条节假日还没填名称。`;
        if([...name].length>ANNUAL_NAME_MAX)return `${year} 年的节假日名称最多 ${ANNUAL_NAME_MAX} 字。`;
        const start=calendarDay(item.start),end=calendarDay(item.end);
        if(!start||!end)return `${year} 年的节假日「${name}」需要完整的开始与结束日期。`;
        if(start.year!==year||end.year!==year)return `${year} 年的节假日日期要落在 ${year} 年内。`;
        if(end.at<start.at)return `${year} 年的节假日「${name}」结束日期早于开始日期。`;
        for(let at=start.at;at<=end.at;at+=86400000) {
          const iso=new Date(at).toISOString().slice(0,10),previous=holidayNames.get(iso);
          if(previous&&previous!==name)return `${iso} 同时属于「${previous}」和「${name}」，同一天只能属于一个节假日。`;
          holidayNames.set(iso,name);days.add(iso);
        }
      }
      const makeup=new Set();
      for(const item of calendar.makeup_workdays) {
        if(!item.date.trim()&&!item.name.trim())continue;
        const day=calendarDay(item.date);
        if(!day)return `${year} 年有一条调休上班没填完整日期。`;
        if(day.year!==year)return `${year} 年的调休上班日期要落在 ${year} 年内。`;
        const iso=item.date,name=item.name.trim()||"调休上班";
        if(holidayNames.has(iso))return `${iso} 已经是节假日，不能再设为调休上班。`;
        if(makeup.has(iso))return `${iso} 重复填了调休上班。`;
        if([...name].length>ANNUAL_NAME_MAX)return `${year} 年的调休名称最多 ${ANNUAL_NAME_MAX} 字。`;
        makeup.add(iso);days.add(iso);
      }
      const terms=new Set();
      for(const item of calendar.solar_terms) {
        if(!item.date.trim()&&!item.name.trim())continue;
        const day=calendarDay(item.date),name=item.name.trim();
        if(!day||!name)return `${year} 年的节气需要同时填日期与名称。`;
        if(day.year!==year)return `${year} 年的节气日期要落在 ${year} 年内。`;
        if([...name].length>ANNUAL_NAME_MAX)return `${year} 年的节气名称最多 ${ANNUAL_NAME_MAX} 字。`;
        if(terms.has(item.date+"\u0000"+name))return `${year} 年的节气「${name}」重复添加了。`;
        terms.add(item.date+"\u0000"+name);days.add(item.date);
      }
      if(days.size>ANNUAL_DAYS_MAX)return `${year} 年标记了 ${days.size} 天，超过 ${ANNUAL_DAYS_MAX} 天的上限。`;
    }
    return "";
  }
  /* 结构层面保持宽松（能开就能编辑），严格规则交给 annualCalendarsProblem。*/
  function validAnnualCalendars(value) {
    if(typeof value!=="string"||new TextEncoder().encode(value).length>ANNUAL_BYTES_MAX)return false;
    if(!value.trim())return true;
    const model=annualCalendars(value);
    if(!model)return false;
    return model.calendars.length>=1&&model.calendars.length<=ANNUAL_YEARS_MAX
      &&new Set(model.calendars.map(row=>row.year)).size===model.calendars.length;
  }
  function validField(name,value) {
    const field=definitions[name];if(!field)return false;
    if(name==="planning_annual_calendars")return validAnnualCalendars(value);
    if(name==="planning_weather_location")return validWeatherLocation(value);
    if(field.type==="boolean")return typeof value==="boolean";
    if(field.type==="number"||field.type==="range")return Number.isInteger(value)&&value>=minimumOf(field)&&value<=field.max;
    if(field.type==="checklist") {
      if(typeof value!=="string")return false;
      const selected=value?value.split(",").map(item=>item.trim()):[];
      return selected.length===new Set(selected).size&&selected.every(item=>Object.hasOwn(field.choices,item));
    }
    if(field.type==="probability")return typeof value==="number"&&Number.isFinite(value)&&value>=0&&value<=1;
    if(field.type==="time")return typeof value==="string"&&isClock(value);
    if(field.type==="select")return typeof value==="string"&&Object.hasOwn(field.choices,value);
    return typeof value==="string"&&(isTimezone(name)?value.length>0&&value.length<=256:[...value].length<=(field.maxLength||2048));
  }
  function normalized(kind,values,locationParts) {
    const result={};
    for(const name of fields(kind)){
      const field=definitions[name];let value=values[name];
      if(name==="maintenance_grant_scope"&&value===undefined)value="role";
      if(name==="planning_weather_location"&&locationParts)value=weatherLocationValue(locationParts);
      if(field.type==="number"||field.type==="range")value=Number(value);
      if(field.type==="probability")value=Number(value);
      if(field.type==="checklist") {
        const selected=new Set(typeof value==="string"?value.split(",").map(item=>item.trim()).filter(Boolean):[]);
        value=Object.keys(field.choices).filter(item=>selected.has(item)).join(",");
      }
      if(isTimezone(name)&&typeof value==="string")value=value.trim();
      /* 年度日历：问题说清楚后，落库前清掉刚添加还没填的行，避免把空行写进后端。*/
      if(name==="planning_annual_calendars") {
        const problem=annualCalendarsProblem(value);
        if(problem)throw new Error(problem);
        result[name]=value.trim()?annualCalendarsValue(annualCalendars(value).calendars,false):"";
        continue;
      }
      if(!validField(name,value))throw new Error(field.type==="number"?`${field.label}请填写 ${rangeText(field)}的整数。`
        :field.type==="probability"?`${field.label}请填写 0 到 1 之间的小数。`
        :field.type==="time"?`${field.label}请填写 24 小时制的 HH:MM，例如 04:00。`
        :`请核对${field.label}。`);
      if(isTimezone(name)){
        try{new Intl.DateTimeFormat("zh-CN",{timeZone:value}).format();}catch{throw new Error("请填写有效的 IANA 时区，例如 Asia/Shanghai。");}
      }
      result[name]=value;
    }
    return result;
  }
  function field(name,values,e) {
    const spec=definitions[name],id="state-config-"+name,value=values[name];
    const base=`id="${id}" data-state-field="${name}"`;
    if(spec.type==="boolean")return `<label class="state-checkbox"><input ${base} type="checkbox" ${value?"checked":""}>${spec.label}</label>`;
    if(spec.type==="checklist") {
      const selected=new Set((value||"").split(",").map(item=>item.trim()));
      return `<fieldset class="state-checklist-field"><legend>${spec.label}</legend><div class="state-checklist">${Object.entries(spec.choices).map(([key,text])=>
        `<label><input type="checkbox" data-state-field="${name}" value="${e(key)}" ${selected.has(key)?"checked":""}><span>${e(text)}</span></label>`).join("")}</div></fieldset>`;
    }
    if(spec.type==="range")return `<label class="state-range-field" for="${id}"><span>${spec.label}</span><span class="state-range-control"><input ${base} type="range" min="${minimumOf(spec)}" max="${spec.max}" step="1" value="${e(value)}"><output id="${id}-output" for="${id}" data-state-output-for="${name}">${e(value)}%</output></span></label>`;
    const input=spec.type==="select"?`<select ${base}>${Object.entries(spec.choices).map(([key,text])=>`<option value="${key}" ${value===key?"selected":""}>${text}</option>`).join("")}</select>`
      :spec.type==="textarea"?`<textarea ${base} rows="3" placeholder="${e(spec.placeholder||"例如：自然表达，不反复播报阶段；不适时可以安静陪伴。")}">${e(value)}</textarea>`
      /* number 必须显式渲染 min：写死 min="1" 会把阶段能量的负值挡在浏览器层，
         用户看到的是「值非法」而不知道原因。probability 用 0–1 的小数框。 */
      :spec.type==="number"?`<input ${base} type="number" min="${minimumOf(spec)}" max="${spec.max}" step="1" required value="${e(value)}">`
      :spec.type==="probability"?`<input ${base} type="number" min="0" max="1" step="0.01" required value="${e(value)}">`
      :spec.type==="time"?`<input ${base} type="time" required value="${e(value)}">`
      :`<input ${base} type="text" autocomplete="off" required value="${e(value)}">`;
    return `<label for="${id}">${spec.label}${input}</label>`;
  }
  function configGroup(index,title,help,content) {
    return `<section class="state-config-group"><header><span class="state-config-index">${String(index).padStart(2,"0")}</span><div><h4>${title}</h4><p>${help}</p></div></header>${content}</section>`;
  }
  function configField(name,values,e,{help="",title=null,tile=false,slider=false,stepper=false,ariaLabel=""}={}) {
    const spec=definitions[name],id="state-config-"+name,value=values[name],text=title||spec.label;
    const base=`id="${id}" data-state-field="${name}"${ariaLabel?` aria-label="${e(ariaLabel)}"`:""}`;
    const copy=`<span class="state-config-copy"><span>${e(text)}</span>${help?`<small id="${id}-help">${e(help)}</small>`:""}</span>`;
    const described=help?` aria-describedby="${id}-help"`:"";
    if(spec.type==="boolean")return `<label class="state-config-row state-config-switch" for="${id}">${copy}<span class="state-config-toggle"><input ${base} type="checkbox" role="switch"${described} ${value?"checked":""}><span aria-hidden="true"></span></span></label>`;
    if(spec.type==="checklist")return `<div class="state-config-row state-config-checklist">${field(name,values,e)}</div>`;
    if(slider||spec.type==="range") {
      const suffix=slider?" / 100":"%";
      return `<label class="state-config-slider" for="${id}"><span>${copy}<output id="${id}-output" for="${id}" data-state-output-suffix="${suffix}">${e(value)}${suffix}</output></span><input ${base} type="range" min="${minimumOf(spec)}" max="${spec.max}" step="1" value="${e(value)}"${described}></label>`;
    }
    if(spec.type==="textarea")return `<label class="state-config-text" for="${id}">${copy}<textarea ${base} rows="3" maxlength="${spec.maxLength||2048}" placeholder="${e(spec.placeholder||"自然表达，不反复播报阶段；不适时可以安静陪伴。")}"${described}>${e(value)}</textarea></label>`;
    const control=spec.type==="select"?`<select ${base}${described}>${Object.entries(spec.choices).map(([key,label])=>`<option value="${key}" ${value===key?"selected":""}>${label}</option>`).join("")}</select>`
      :spec.type==="number"?`<input ${base} type="number" min="${minimumOf(spec)}" max="${spec.max}" step="1" required value="${e(value)}"${described}>`
        :spec.type==="probability"?`<input ${base} type="number" min="0" max="1" step="0.01" required value="${e(value)}"${described}>`
          :`<input ${base} type="${spec.type==="time"?"time":"text"}" ${spec.type==="time"||isTimezone(name)?"required":""}${spec.maxLength?` maxlength="${spec.maxLength}"`:""} autocomplete="off" value="${e(value)}"${described}>`;
    if(stepper)return `<div class="state-config-row"><label class="state-config-copy" for="${id}"><span>${e(text)}</span><small id="${id}-help">${e(help)}</small></label><div class="state-config-stepper"><button type="button" data-state-adjust="${name}" data-state-step="-1" aria-label="减少${e(text)}">−</button>${control}<span>天</span><button type="button" data-state-adjust="${name}" data-state-step="1" aria-label="增加${e(text)}">+</button></div></div>`;
    return `<label class="state-config-row${tile?" state-config-tile":""}" for="${id}">${copy}${control}</label>`;
  }
  /* 年度日历的三段行与年份卡。id 必须稳定：state-page.render() 靠元素 id
     在重绘后恢复焦点，所以「添加一行」重绘时光标不会丢。*/
  function annualCalendarRow(section,item,index,row,year,e) {
    const path=index+"."+section.key+"."+row,min=year?year+"-01-01":"",max=year?year+"-12-31":"";
    const controls=section.fields.map((field,position)=>{
      const id="state-annual-"+index+"-"+section.key+"-"+row+"-"+field.name;
      const label=section.title+field.label;
      const control=field.type==="date"
        ?`<input id="${id}" data-state-annual="${path}.${field.name}" type="date" min="${min}" max="${max}" value="${e(item[field.name])}" aria-label="${e(label)}">`
        :`<input id="${id}" data-state-annual="${path}.${field.name}" type="text" maxlength="${ANNUAL_NAME_MAX}" autocomplete="off" value="${e(item[field.name])}" placeholder="${e(field.placeholder||"")}" aria-label="${e(label)}">`;
      return (position===2&&section.key==="holidays")?'<span class="state-annual-sep" aria-hidden="true">至</span>'+control:control;
    }).join("");
    return `<div class="state-annual-row is-${section.key}">${controls}<button type="button" class="state-annual-remove" data-state-annual-action="remove-row" data-state-annual-group="${section.key}" data-state-annual-index="${index}" data-state-annual-row="${row}" aria-label="删除这条${e(section.title)}" title="删除这条"><i data-lucide="x" aria-hidden="true"></i></button></div>`;
  }
  function annualCalendarCard(calendar,index,e) {
    const year=Number.isInteger(Number(calendar.year))?String(calendar.year):"",counts=annualCounts(calendar),marks=annualMarks(calendar);
    const sections=ANNUAL_SECTIONS.map(section=>{
      const list=calendar[section.key]||[];
      const rows=list.map((item,row)=>annualCalendarRow(section,item,index,row,year,e)).join("");
      return `<section class="state-annual-section"><header><h5>${section.title}<span>${list.length}</span></h5><p>${section.help}</p></header>
        ${rows?`<div class="state-annual-rows">${rows}</div>`:""}
        <button type="button" class="state-annual-addrow" data-state-annual-action="add-row" data-state-annual-group="${section.key}" data-state-annual-index="${index}"><i data-lucide="plus" aria-hidden="true"></i>${section.add}</button></section>`;
    }).join("");
    const strip=marks.some(count=>count)?`<div class="state-annual-months" role="img" aria-label="各月已标记天数">${marks.map((count,month)=>
      `<span class="${count?"is-marked":""}" title="${ANNUAL_MONTHS[month]}月${count?" · 已标记 "+count+" 天":" · 未标记"}"><b>${count||""}</b><small>${ANNUAL_MONTHS[month]}</small></span>`).join("")}</div>`:"";
    return `<section class="state-annual-card">
      <header class="state-annual-card-head">
        <span class="state-annual-year"><input id="state-annual-${index}-year" data-state-annual="${index}.year" type="number" min="1" max="9999" step="1" inputmode="numeric" value="${e(year)}" aria-label="年份"><span>年</span></span>
        <small>节假日 ${counts.holidays} 天 · 调休 ${counts.makeup} 天 · 节气 ${counts.terms} 项</small>
        <button type="button" class="state-annual-remove is-year" data-state-annual-action="remove-year" data-state-annual-index="${index}" aria-label="删除这个年份的日历">删除年份</button>
      </header>
      ${strip}
      ${sections}</section>`;
  }
  function annualCalendarFields(values,e) {
    const raw=values.planning_annual_calendars;
    const model=annualCalendars(raw),problem=annualCalendarsProblem(raw),calendars=model?model.calendars:[];
    const head=`<div class="state-annual-head"><div><span>按年份填写</span><small>每个年份一组；最多 ${ANNUAL_YEARS_MAX} 个年份、共 16 KB。</small></div>
      <button type="button" class="state-annual-add" data-state-annual-action="add-year" ${calendars.length>=ANNUAL_YEARS_MAX?"disabled":""}>+ 添加年份</button></div>`;
    const empty=calendars.length?"":`<p class="state-annual-empty">还没有配置年份。内置日历已包含固定节假日；需要补充调休、节气或自定义假期时再加一个年份。</p>`;
    return `<div class="state-annual">${head}
      ${problem?`<p class="state-annual-problem" role="alert"><i data-lucide="triangle-alert" aria-hidden="true"></i>${e(problem)}</p>`:""}
      ${calendars.map((calendar,index)=>annualCalendarCard(calendar,index,e)).join("")}
      ${empty}
      <details class="state-annual-advanced"${model?"":" open"}><summary>高级 · 原始数据</summary>
        <p class="state-planning-note">上面的表单和这里是同一份数据，改动会互相同步。只有批量导入或粘贴已有数据时才需要直接编辑。</p>
        <label for="state-config-planning_annual_calendars"><span>年度日历原始数据（JSON 数组）</span><textarea id="state-config-planning_annual_calendars" data-state-field="planning_annual_calendars" rows="4" maxlength="${ANNUAL_BYTES_MAX}" spellcheck="false" placeholder="${e(definitions.planning_annual_calendars.placeholder)}">${e(raw)}</textarea></label>
        <p class="state-planning-note">每个年份一项：<code>schema</code> 为 <code>${ANNUAL_SCHEMA}</code>，日期用 <code>YYYY-MM-DD</code>。</p>
      </details></div>`;
  }
  function weatherLocationFields(values,e) {
    const location=weatherLocationParts(values.planning_weather_location);
    return `<div class="state-weather-location-fields">
      <label for="state-weather-location-name">角色所在地<input id="state-weather-location-name" data-state-weather-location="name" type="text" maxlength="64" autocomplete="address-level2" value="${e(location.name)}" placeholder="城市、区县或镇街"></label>
      <details><summary>坐标与官方预警区域（可选）</summary><div class="state-weather-location-advanced">
        <label for="state-weather-location-latitude">纬度<input id="state-weather-location-latitude" data-state-weather-location="latitude" type="number" min="-90" max="90" step="any" value="${e(location.latitude)}" placeholder="留空自动解析"></label>
        <label for="state-weather-location-longitude">经度<input id="state-weather-location-longitude" data-state-weather-location="longitude" type="number" min="-180" max="180" step="any" value="${e(location.longitude)}" placeholder="留空自动解析"></label>
        <label for="state-weather-alert-city">预警城市<input id="state-weather-alert-city" data-state-weather-location="alert_city" type="text" maxlength="64" value="${e(location.alert_city)}"></label>
        <label for="state-weather-alert-area">预警区域<input id="state-weather-alert-area" data-state-weather-location="alert_area" type="text" maxlength="64" value="${e(location.alert_area)}"></label>
      </div><p class="state-planning-note">坐标需成对填写；精确官方预警需要同时填写城市和区域。</p></details>
    </div>`;
  }
  function snapshot(status) {
    return {revision:status.state.revision,view_token:status.state.view_token,settings_token:status.cycle_settings_token,
      settings:structuredClone(status.settings),timeline:structuredClone(status.state.cycle_timeline||null)};
  }
  function canAlign(status) {
    return !!status?.settings?.cycle_enabled&&status.state?.state!=="unavailable"
      &&typeof status.state?.view_token==="string"&&typeof status.cycle_settings_token==="string";
  }
  function alignment(draft) {
    const mode=draft.values.applicability;
    if(!Object.hasOwn(applicability,mode))throw new Error("请选择这次周期校准的方式。");
    const day=mode==="applicable"?Number(draft.values.day):null;
    const max=length(draft.base.settings);
    if(mode==="applicable"&&(!Number.isInteger(day)||day<1||day>max))throw new Error(`当前配置的周期为 ${max} 天，请填写 1–${max} 之间的周期日。`);
    return {action:"cycle.align",expected_revision:draft.base.revision,view_token:draft.base.view_token,
      settings_token:draft.base.settings_token,data:{applicability:mode,day}};
  }
  function form(draft,e) {
    const values=draft.values;
    if(draft.kind==="settings")return configGroup(1,"展示开关","关闭后不再请求对应展示；授权未通过时不会发送。",
      ["typing_enabled","presence_enabled","typing_refresh_seconds"].map(name=>configField(name,values,e,{tile:name==="typing_refresh_seconds",help:name==="typing_refresh_seconds"?"取值为 1–10 秒；只在当前会话有进行中的回复时刷新。":""})).join(""));
    if(draft.kind==="home") {
      return configGroup(1,"后台状态维护","定期更新角色的身体与心理状态，设置随当前人格共享；与「每日状态巡视」不同，巡视只定时看一眼并留记录，不改数值。",
        configField("enabled",values,e,{title:"启用状态维护",help:"关闭后暂停后台更新，已有状态记录保留。"})
        +configField("maintenance_grant_scope",values,e,{title:"后台维护授权范围",help:"默认整个角色：开启一次后，该角色的其他私聊窗口自动获得维护授权；仅当前窗口：每个私聊单独授权，适合一个角色被多个用户共用。"})
        +configField("maintenance_minutes",values,e,{title:"更新间隔（分钟）",help:"1–1440 分钟；保存后用于后续维护安排。"}))
        +configGroup(2,"状态模拟与首页展示","状态模拟开关控制后台是否更新这一项；首页展示只控制小屋首页是否显示，状态详情仍可查看。睡眠状态模拟与按角色作息自动入睡是两个独立开关。",
          `<div class="state-home-display-summary"><span data-state-home-count>${Object.keys(homeFeatures).filter(name=>values[name+"_enabled"]&&values["home_show_"+name]).length} 项允许展示</span><small>特殊状态依据已有记录判断</small></div><div class="state-home-feature-list">${Object.entries(homeFeatures).map(([name,spec])=>{
            const special="home_only_special_"+name,id="state-config-"+special;
            return `<article class="state-home-feature" data-state-home-feature="${name}"><div class="state-home-feature-copy"><i data-lucide="${spec.icon}" aria-hidden="true"></i><div><h5>${spec.label}</h5><p>${spec.help}</p></div></div><div class="state-home-feature-controls">${configField(name+"_enabled",values,e,{title:"状态模拟",ariaLabel:spec.label+"状态模拟"})}${configField("home_show_"+name,values,e,{title:"首页展示",ariaLabel:spec.label+"首页展示"})}${spec.special?`<label class="state-home-display-policy" for="${id}"><span class="sr-only">${spec.label}展示条件</span><select id="${id}" data-state-field="${special}" data-state-display-policy ${values["home_show_"+name]?"":"disabled"}><option value="always" ${values[special]?"":"selected"}>始终展示</option><option value="special" ${values[special]?"selected":""}>${spec.special}</option></select></label>`:'<span class="state-home-display-policy-note">始终展示</span>'}</div></article>`;
          }).join("")}</div><p class="state-home-special-note">仅特殊状态展示时，正常、未知或未记录的值会隐藏；状态模拟关闭的项目也不会出现在首页。</p>`);
    }
    if(draft.kind==="intensity")return configGroup(1,"表达强度","0 表示不注入状态表达；数值越高，身体与情绪的余波越容易自然影响回复语气。",
      configField("humanized_state_intensity",values,e,{slider:true}))
      +configGroup(2,"精力模拟引导","引导精力的模拟倾向，不直接设定数值；留空使用默认表现，最多 512 字。",configField("energy_expression_prompt",values,e));
    if(draft.kind==="datetime")return configGroup(1,"角色当地时间","小屋时钟、今日安排与日程规划共用这个时区。",configField("planning_timezone",values,e,{title:"小屋与日程时区",tile:true,help:"使用 IANA 时区名称，例如 Asia/Shanghai 或 Asia/Tokyo。"}));
    if(draft.kind==="planning") {
      const modes={
        continuous:["连续日程（HDSI）","收到消息时，围绕已接受的角色计划自然回应；自动编排暂停。"],
        orchestrated:["日程编排","每天规划安排，每周补充长期节点，并在临近时细化。"]
      };
      const modeChoices=Object.entries(modes).map(([key,[title,description]])=>`<label class="state-planning-mode-option${values.schedule_simulation_mode===key?" is-selected":""}" for="state-config-schedule-mode-${key}">
        <input id="state-config-schedule-mode-${key}" type="radio" name="schedule_simulation_mode" data-state-field="schedule_simulation_mode" value="${key}" ${values.schedule_simulation_mode===key?"checked":""}>
        <span class="state-planning-mode-copy"><strong>${title}</strong><small>${description}</small></span><i data-lucide="check" aria-hidden="true"></i></label>`).join("");
      return `<div class="state-planning-form">
        ${configGroup(1,"角色日程","先决定角色是否在私聊中使用日程，再选择一种运行方式。",
          `${configField("calendar_enabled",values,e,{help:"对同一角色的所有私聊生效；每个私聊的安排仍相互隔离。"})}
          <fieldset class="state-planning-mode-options"><legend>选择一种模式</legend>${modeChoices}</fieldset>
          ${configField("planning_enabled",values,e,{help:"仅在「日程编排」模式下自动生成计划。"})}`)}
        ${configGroup(2,"编排范围","这些参数决定计划覆盖的时间跨度与细化节奏。",`<div class="state-config-grid">${["planning_timezone","planning_horizon_days","planning_refine_minutes","planning_max_items"].map(name=>configField(name,values,e,{tile:true})).join("")}</div>`)}
        ${configGroup(3,"年度日历","按年份填写节假日、调休与节气；不填写时沿用默认日历。",annualCalendarFields(values,e))}
        ${configGroup(4,"生活偏好","为规划提供方向，角色仍可结合当下对话自行调整。",configField("planning_prompt",values,e,{help:"最多 4096 字，留空使用默认规划。自动规划需先启用角色日程并选择「日程编排」。"}))}
      </div>`;
    }
    if(draft.kind==="weather")return `<div class="state-weather-settings-form">
      ${configGroup(1,"天气感知","同一角色的所有私聊共用此开关；按授权按需读取所在地实况。",`${configField("planning_weather_enabled",values,e)}<div class="state-config-grid state-config-weather-grid">${weatherLocationFields(values,e)}${configField("planning_weather_max_age_minutes",values,e,{tile:true,help:"取值为 5–120 分钟；超时会按授权重新读取。"})}</div>`)}
      <p class="state-planning-note">配置和风凭据时使用和风；未配置时自动使用免密 Open-Meteo。此处不保存 API 凭据。</p>
    </div>`;
    if(draft.kind==="health"||draft.kind==="hunger") {
      const name=draft.kind==="health"?"健康感受":"饥饿感受";
      const tip=draft.kind==="health"?"只表达角色自己的主观体感；不推断疾病，也不把短暂不适写成诊断。":"结合作息与活动自然变化，不把聊天中的吃饭建议当成已经进食。";
      return configGroup(1,"模拟开关",`关闭后不再演化${name}，已有记录保留；开关跟随当前人格，在所有已授权会话中一致。`,configField(draft.kind+"_enabled",values,e))
        +configGroup(2,"表达引导",tip+" 最多 512 字，留空使用默认表现。",configField(draft.kind+"_expression_prompt",values,e));
    }
    if(draft.kind==="maintenance") {
      const names=fields(draft.kind),title="角色状态模拟";
      const help="这些偏好跟随当前人格，在它已授权的会话中保持一致。";
      return `<h4 class="state-form-section">${title}</h4><p class="state-edit-help">${help}</p>
        <div class="state-edit-fields">${names.map(name=>field(name,values,e)).join("")}</div>`;
    }
    if(draft.kind==="cycle") {
      const phase=Object.hasOwn(phases,selectedPhase)?selectedPhase:"menstrual";
      const phaseLabel=phases[phase], phaseDays=values["cycle_"+phase+"_days"];
      const phaseTabs=Object.entries(phases).map(([key,text])=>`<button type="button" id="state-cycle-phase-${key}" class="state-phase-tab${key===phase?" is-active":""}" role="tab" aria-selected="${key===phase}" aria-controls="state-phase-editor" data-state-phase="${key}" tabindex="${key===phase?"0":"-1"}"><span>${text}</span><small>${e(values["cycle_"+key+"_days"])} 天</small></button>`).join("");
      const total=length(values);
      const durationSegments=Object.entries(phases).map(([key,text])=>`<span data-cycle-segment="${key}" style="--phase-share:${total?Number(values["cycle_"+key+"_days"])/total*100:0}%" title="${text} · ${e(values["cycle_"+key+"_days"])} 天"></span>`).join("");
      const viewTabs=`<div class="state-cycle-view-tabs" role="tablist" aria-label="周期配置视图">
        <button type="button" id="state-cycle-view-plan" class="state-cycle-view-tab${selectedCycleView==="plan"?" is-active":""}" role="tab" aria-selected="${selectedCycleView==="plan"}" aria-controls="state-cycle-view-panel" data-cycle-view="plan" tabindex="${selectedCycleView==="plan"?"0":"-1"}"><span>周期方案</span><small>模拟方式、时区与阶段时长</small></button>
        <button type="button" id="state-cycle-view-expression" class="state-cycle-view-tab${selectedCycleView==="expression"?" is-active":""}" role="tab" aria-selected="${selectedCycleView==="expression"}" aria-controls="state-cycle-view-panel" data-cycle-view="expression" tabindex="${selectedCycleView==="expression"?"0":"-1"}"><span>阶段表达</span><small>按阶段编辑精力与语气参考</small></button>
      </div>`;
      const planView=`<div class="state-cycle-setup" aria-label="周期方案">
        ${configGroup(1,"通用设置","阶段时长与波动从下一轮周期起采用；定位设置不会重写已有起点。",`<div class="state-config-grid">${["cycle_enabled","cycle_mode","cycle_timezone","cycle_initialization","cycle_variation_enabled","cycle_link_intensity"].map(name=>configField(name,values,e,{tile:true})).join("")}</div>`)}
        ${configGroup(2,"不适模拟","概率与体感类型作为柔性提示，不保证精确命中。",`${configField("cycle_discomfort_enabled",values,e)}<fieldset class="state-config-discomfort" data-cycle-discomfort-fields ${values.cycle_discomfort_enabled?"":"disabled"}><legend class="sr-only">不适参考概率与可选体感</legend>${configField("cycle_discomfort_chance",values,e,{title:"不适参考概率"})}${configField("cycle_discomfort_types",values,e)}</fieldset>`)}
        ${configGroup(3,"阶段时长",`六阶段合计 <strong data-cycle-total>${e(total)}</strong> 天；基础三阶段模式会把中间阶段合并展示。`,
          `<div class="state-cycle-duration-overview"><div class="state-cycle-meter" role="img" aria-label="六阶段时长分布">${durationSegments}</div></div><div class="state-config-phase-grid">${Object.keys(phases).map(key=>`<div class="state-config-duration${draft.currentPhase===key?" is-current":""}">${configField("cycle_"+key+"_days",values,e,{tile:true})}${draft.currentPhase===key?'<span class="state-config-current-mark">当前阶段</span>':""}</div>`).join("")}</div>`)}
      </div>`;
      const expressionView=`<div class="state-config-expression" aria-label="阶段表达参数">${configGroup(1,"编辑一个阶段","只显示选中阶段；精力是趋势参考，氛围词是语气线索，表达引导提供可选方向。",`
        <div class="state-phase-tabs" role="tablist" aria-label="选择周期阶段">${phaseTabs}</div>
        <fieldset id="state-phase-editor" class="state-phase-editor" role="tabpanel" aria-labelledby="state-cycle-phase-${phase}"><legend><span>${phaseLabel}</span><small>本阶段设为 ${e(phaseDays)} 天</small></legend>
          <p class="state-phase-help">${e(phaseDescriptions[phase])}。氛围词不是情绪结论，实际表达仍以角色当下感受和话题为准。</p>
          <div class="state-config-grid">${configField("cycle_"+phase+"_energy",values,e,{tile:true,help:"取值 −50 至 30；负值表示该阶段精力偏低。"})}${configField("cycle_"+phase+"_mood",values,e,{tile:true,help:"最多 80 字，作为语气参考。"})}</div>
          ${configField("cycle_"+phase+"_prompt",values,e,{help:"最多 512 字，留空使用默认表现。"})}
        </fieldset>`)}${configGroup(2,"整体表达偏好","所有阶段共用的柔性提示，不会覆盖角色原人格。",configField("cycle_expression_prompt",values,e,{help:"最多 2048 字，可留空使用默认表达。"}))}</div>`;
      return `${viewTabs}<div id="state-cycle-view-panel" class="state-cycle-view-panel" role="tabpanel" tabindex="0" aria-labelledby="state-cycle-view-${selectedCycleView}">${selectedCycleView==="expression"?expressionView:planView}</div>`;
    }
    if(draft.kind==="mood")return configGroup(1,"心情动力学","概率决定这一轮情绪是否变化，幅度决定一次最多变化多少，半衰期决定多久回归基线。",
      configField("mood_enabled",values,e)+configField("mood_update_probability",values,e,{help:"取值 0–1；例如 0.35 表示 35%。"})+configField("mood_update_max_delta",values,e,{help:"取值 1–4；控制单轮的最大变化。"})+configField("mood_decay_half_life_minutes",values,e,{help:"取值 5–1440 分钟；半衰期越短，越快回归基线。"}));
    if(draft.kind==="patrol")return configGroup(1,"每日状态巡视","只查看并记录，不推进数值（推进见「后台状态维护」）；使用角色本地时区，超过保留天数的报告会清除。",
      configField("daily_state_patrol_enabled",values,e)+configField("daily_state_patrol_time",values,e,{title:"巡视时刻",help:"按角色本地时区执行；建议在对话最少的时段运行。"})+configField("daily_state_patrol_retention_days",values,e,{title:"报告保留天数",stepper:true,help:"超过天数自动清除，取值范围 3–180 天。"}));
    const base=draft.base,current=base.timeline,dayEnabled=values.applicability==="applicable";
    const observed=current?.cycle_day?`第 ${current.cycle_day} / ${current.cycle_days} 天`:
      ({not_applicable:"不适用",unknown:"尚未定位"}[current?.applicability]||"尚未定位");
    return `<p class="state-edit-help">当前记录：${e(observed)}。本次采用周期 ${e(length(base.settings))} 天、时区 ${e(base.settings.cycle_timezone)}；同一角色的其他已授权会话会采用校准后的时间线。</p>
      <div class="state-edit-fields"><label for="state-cycle-action">校准方式<select id="state-cycle-action" data-state-field="applicability" required><option value="">请选择</option>${Object.entries(applicability).map(([key,text])=>`<option value="${key}" ${key===values.applicability?"selected":""}>${text}</option>`).join("")}</select></label>
      <label for="state-cycle-day">今天是第几天<input id="state-cycle-day" type="number" min="1" max="${e(length(base.settings))}" step="1" data-state-field="day" value="${e(values.day)}" ${dayEnabled?"required":"disabled"}></label></div>
      <p class="state-edit-help">保存会重建周期定位并清除旧周期感受；“交回角色判断”在下次维护采纳。</p>`;
  }
  function summary(status,e) {
    const plan=status.state?.cycle_timeline?.pending_plan;
    return `<header class="state-maintenance-heading"><div><h3>身体与心情配置</h3></div><div class="state-scope-legend" aria-label="设置范围"><span>当前会话</span><span>同一 Bot 下的角色共享</span><span>当前角色校准</span></div></header>
      ${plan?`<p class="state-edit-help">已排定的新周期参数从 ${e(plan.effective_date)} 开始采用。</p>`:""}`;
  }
  function planningSummary(status,e) {
    const settings=status.settings||{},planning=status.planning;
    const supported=fields("planning").every(key=>validField(key,settings[key]));
    const heading=`<header class="state-planning-panel-heading"><h3>日程运行情况</h3></header>`;
    if(!supported)return heading+`<p class="state-edit-help">自动规划设置暂未完整读到，请重新读取状态后再查看。</p>`;
    const enabled=typeof planning?.enabled==="boolean"?planning.enabled:settings.planning_enabled;
    const calendar=settings.calendar_enabled===true;
    const scheduleMode=settings.schedule_simulation_mode;
    const continuous=scheduleMode==="continuous";
    const state=!calendar?"角色日程已关闭":continuous?"对话时接续现有计划"
      :!enabled?"自动规划已关闭":planning?.running?"正在规划":planning?"等待下次检查":"运行状态尚未提供";
    const mode={daily:"当天安排",horizon:"长期节点",refine:"临近细化"};
    const result=planning?.last_result;
    const resultState={succeeded:"已完成",blocked:"条件未满足",deferred:"本地预算允许后自动恢复",failed:"本次未完成",cancelled:"已取消",interrupted:"已中断"};
    const last=result?[mode[result.mode]||"规划任务",resultState[result.state]||"已有处理记录"].join(" · "):"尚无规划记录";
    const rejected=Array.isArray(result?.rejected)?result.rejected.length:result?.rejected;
    const counts=result?[Number.isInteger(result.saved)&&result.saved>=0?`保存 ${result.saved} 项`:null,
      Number.isInteger(rejected)&&rejected>=0?`未采用 ${rejected} 项`:null].filter(Boolean).join(" · "):"";
    const next=typeof planning?.next_check==="string"?new Date(planning.next_check):null;
    const nextText=next&&!Number.isNaN(next.valueOf())?next.toLocaleString("zh-CN",{hour12:false}):next?"检查时间暂不可用":"尚未排程";
    return heading+`<div class="state-planning-statusbar"><div><span class="state-planning-status-dot ${calendar&&continuous?"is-continuous":enabled&&calendar?"is-active":"is-paused"}"></span><div><small>当前运行</small><strong>${e(state)}</strong></div></div>
      <div class="state-planning-mode-label"><small>所选模式</small><strong>${e(scheduleModes[scheduleMode]||"暂不可用")}</strong></div></div>
      <dl class="state-maintenance-summary"><div><dt>角色日程</dt><dd>${calendar?"全局已启用 · 私聊安排彼此隔离":"全局关闭"}</dd></div>
      <div><dt>安排节奏</dt><dd>${continuous?"结合已接受的计划回应；自动编排暂停":"每天安排 · 每周补充长期节点 · 提前 "+e(settings.planning_refine_minutes)+" 分钟细化"}</dd></div>
      <div><dt>规划范围</dt><dd>${e(settings.planning_horizon_days)} 天 · 每次最多 ${e(settings.planning_max_items)} 项 · ${e(settings.planning_timezone)}</dd></div>
      <div><dt>天气参考</dt><dd>${settings.planning_weather_enabled?"已开启 · "+e(weatherLocationParts(settings.planning_weather_location).name||"地点待配置")+" · 沿用 "+e(settings.planning_weather_max_age_minutes)+" 分钟":"已关闭"}</dd></div>
      <div><dt>最近一次</dt><dd>${e(last)}${counts?` · ${e(counts)}`:""}</dd></div><div><dt>下次检查</dt><dd>${e(nextText)}</dd></div></dl>
      ${result?.code||result?.task_id?`<details class="state-receipt-reference" data-receipt="${e("planning:"+(result.task_id||result.mode||"latest"))}"><summary id="state-planning-receipt-summary">查看规划处理记录</summary><p>${e(last)}${counts?` · ${e(counts)}`:""}</p>${result.code?`<p>${e(({host_not_running:"角色尚未运行，请重新读取状态。",calendar_disabled:"角色日程总开关已关闭，请在本页启用。",authorization_denied:"当前身份未通过会话授权，请核对角色与私聊绑定。",model_task_unregistered:"规划模型尚未就绪，请检查模型设置。"})[result.code] || "本次规划未完成，请检查角色日程开关与模型设置。")}</p>`:""}</details>`:""}
      ${continuous?`<p class="state-edit-help state-planning-callout state-planning-callout-continuous">连续模式会把当前已接受的角色计划带入回复；普通消息仍保存在对话历史中，不会被逐条伪装成已完成的日程。</p>`:""}`;
  }
  const titles={home:"状态与首页展示",intensity:"能量表达设置",maintenance:"身体状态设置",
    health:"健康设置",hunger:"饥饿设置",
    planning:"日程与规划",weather:"天气感知",datetime:"时间与日期",cycle:"生理期参数",align:"周期校准",settings:"QQ 展示设置",
    mood:"心情动力学",patrol:"每日状态巡视"};
  function pendingText(command) {
    if(command.action!=="cycle.align")return null;
    return `周期校准：${applicability[command.data.applicability]}${command.data.day!==null?` · 第 ${command.data.day} 天`:""}`;
  }
  function actionSummary(kind,status,e) {
    const settings=status.settings||{},days=length(settings),timeline=status.state?.cycle_timeline;
    if(kind==="maintenance")return `${settings.health_enabled?"健康开启":"健康关闭"} · ${settings.hunger_enabled?"饥饿开启":"饥饿关闭"} · 当前人格共享`;
    if(kind==="health")return `${settings.health_enabled?"健康感受模拟已开启":"健康感受模拟已关闭"} · ${settings.health_expression_prompt?"已设引导":"使用默认表现"} · 当前人格共享`;
    if(kind==="hunger")return `${settings.hunger_enabled?"饥饿感受模拟已开启":"饥饿感受模拟已关闭"} · ${settings.hunger_expression_prompt?"已设引导":"使用默认表现"} · 当前人格共享`;
    if(kind==="home")return `${settings.enabled?"状态维护已开启":"状态维护已关闭"} · 每 ${e(settings.maintenance_minutes??"未提供")} 分钟更新`;
    if(kind==="intensity")return `${e(settings.humanized_state_intensity??"未提供")} / 100 · ${settings.energy_expression_prompt?"已设精力引导":"使用默认表现"} · 当前人格共享`;
    if(kind==="cycle")return `${settings.cycle_enabled?"已开启":"未启用"} · ${e(modes[settings.cycle_mode]||"阶段模式待核对")} · ${e(Number.isFinite(days)?days:"未提供")} 天 · 当前人格共享`;
    if(kind==="align")return timeline?.cycle_day?`当前第 ${e(timeline.cycle_day)} / ${e(timeline.cycle_days)} 天 · 按实际设定校准`:
      timeline?.applicability==="not_applicable"?"当前角色标记为不适用":timeline?.applicability==="unknown"?"当前周期尚未定位":"校准当前角色的周期位置";
    if(kind==="mood")return `概率 ${e(settings.mood_update_probability??"未提供")} · 幅度 ${e(settings.mood_update_max_delta??"未提供")} · 半衰期 ${e(settings.mood_decay_half_life_minutes??"未提供")} 分钟 · 当前人格共享`;
    if(kind==="patrol")return `${settings.daily_state_patrol_enabled?"已开启":"已关闭"} · ${e(settings.daily_state_patrol_time||"时刻待提供")} · 保留 ${e(settings.daily_state_patrol_retention_days??"未提供")} 天 · 当前人格共享`;
    if(kind==="weather")return `${settings.planning_weather_enabled?"已开启":"已关闭"} · ${e(weatherLocationParts(settings.planning_weather_location).name||"尚未设置地点")} · 信息时效 ${e(settings.planning_weather_max_age_minutes??"未提供")} 分钟 · 当前角色共享`;
    return "查看并调整当前设置";
  }
  function syncDurationPreview(root) {
    if(!root)return;
    const values=Object.fromEntries(Object.keys(phases).map(key=>[key,Number(root.querySelector(`#state-config-cycle_${key}_days`)?.value)]));
    const total=Object.values(values).reduce((sum,value)=>sum+(Number.isFinite(value)&&value>0?value:0),0);
    const display=root.querySelector("[data-cycle-total]");if(display)display.textContent=String(total);
    for(const key of Object.keys(phases)) {
      const segment=root.querySelector(`[data-cycle-segment="${key}"]`),value=values[key];
      if(segment)segment.style.setProperty("--phase-share",`${total&&Number.isFinite(value)&&value>0?value/total*100:0}%`);
    }
  }
  function selectPhase(key) {if(Object.hasOwn(phases,key))selectedPhase=key;}
  function selectCycleView(view) {if(view==="plan"||view==="expression")selectedCycleView=view;}
  function resetPhase() {selectedPhase="menstrual";selectedCycleView="plan";}
  /* 结构化输入：按 data-state-annual 的路径就地改一个字段，整体序列化回
     planning_annual_calendars。解析不出来就原样返回，避免把看不懂的数据悄悄改写。*/
  function applyAnnualInput(raw, token, value) {
    const model=annualCalendars(raw);
    if(!model)return annualText(raw);
    const parts=token.split("."), index=Number(parts[0]), calendar=model.calendars[index];
    if(!calendar)return annualText(raw);
    if(parts.length===2&&parts[1]==="year") {
      calendar.year=value===""?"":value;
    } else if(parts.length===4) {
      const list=calendar[parts[1]];
      if(!Array.isArray(list)||!list[Number(parts[2])])return annualText(raw);
      list[Number(parts[2])][parts[3]]=value;
    } else return annualText(raw);
    return annualCalendarsValue(model.calendars);
  }
  /* 添加／删除年份与条目。data 取按钮上的 data-state-annual-* 属性。*/
  function annualAction(raw, action, data) {
    const model=annualCalendars(raw)||{calendars:[]}, calendars=model.calendars;
    if(action==="add-year") {
      if(calendars.length>=ANNUAL_YEARS_MAX)return annualText(raw);
      calendars.push({year:"",holidays:[],makeup_workdays:[],solar_terms:[]});
    } else if(action==="remove-year") {
      const index=Number(data.index);
      if(Number.isInteger(index)&&index>=0&&index<calendars.length)calendars.splice(index,1);
    } else if(action==="add-row") {
      const calendar=calendars[Number(data.index)];
      if(!calendar||!ANNUAL_SECTIONS.some(section=>section.key===data.group))return annualText(raw);
      const list=calendar[data.group]||(calendar[data.group]=[]);
      list.push(data.group==="holidays"?{name:"",start:"",end:""}:{date:"",name:""});
    } else if(action==="remove-row") {
      const list=calendars[Number(data.index)]?.[data.group];
      if(Array.isArray(list)) {const row=Number(data.row);if(Number.isInteger(row)&&row>=0&&row<list.length)list.splice(row,1);}
    } else return annualText(raw);
    return annualCalendarsValue(calendars);
  }
  function toggleChecklist(name,current,value,checked) {
    const spec=definitions[name];if(spec?.type!=="checklist")return current;
    const selected=new Set(String(current||"").split(",").map(item=>item.trim()).filter(Boolean));
    if(checked)selected.add(value);else selected.delete(value);
    return Object.keys(spec.choices).filter(item=>selected.has(item)).join(",");
  }
  /* 全局搜索用。role.state 这一组 85 个字段此前整组不在搜索索引里，
     搜「生理期」「心情」都只能靠记住它在状态页的哪一格。
     QQ 展示设置不在 definitions 里（它由 state-editor 自己渲染表单），单独补三条。 */
  const presentationFields=[
    ["typing_enabled","启用正在输入提示","仅对所选私聊窗口生效；从收到消息并通过身份校验开始，持续到最后一条分段发送结束。"],
    ["typing_refresh_seconds","输入提示刷新间隔（秒）","每隔几秒续一次「正在输入」，取值 1–10。"],
    ["presence_enabled","启用 QQ 自定义短状态","把角色的公开短文案显示成 QQ 自定义状态。"],
    ["online_status_enabled","启用 QQ 在线状态","按角色的模拟状态上报在线／离开／忙碌。"],
  ];
  function searchEntries() {
    const kinds=Object.keys(titles).filter(kind=>groups[kind]);
    const entries=kinds.flatMap(kind=>(groups[kind]||[]).filter(name=>definitions[name]).map(field=>({
      group:"role.state",groupTitle:"角色状态",field,
      label:definitions[field].label||field,help:definitions[field].help||"",
      section:kind,sectionTitle:titles[kind],sectionCount:kinds.length,
    })));
    return entries.concat(presentationFields.map(([field,label,help])=>({
      group:"role.state",groupTitle:"角色状态",field,label,help,
      section:"settings",sectionTitle:titles.settings,sectionCount:kinds.length,
    })));
  }
  return {fields,label,validField,normalized,weatherLocationParts,weatherLocationValue,toggleChecklist,applyAnnualInput,annualAction,snapshot,canAlign,alignment,form,summary,planningSummary,titles,homeFeatures,pendingText,phases,actionSummary,syncDurationPreview,selectPhase,selectCycleView,resetPhase,searchEntries};
})();
