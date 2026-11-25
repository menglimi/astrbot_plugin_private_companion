/* Zero-dependency fuzzy search engine and unified search registry for Companion V2.
 *
 * Five-layer fusion scoring (< 5ms for 1,500+ entries):
 *   1. Exact / prefix / substring across title, field key, aliases, section/breadcrumb, help
 *   2. Bidirectional synonym and legacy-name alias expansion
 *   3. Unspaced Chinese segmentation / 2-gram soft recall coverage
 *   4. Full-pinyin (syllable-aligned) and pinyin-initials matching (with polyphone support)
 *   5. 1-char typo tolerance (Damerau-Levenshtein <= 1 + compact ordered subsequence)
 */
"use strict";
(() => {
  const PINYIN_BY_SYLLABLE = {"a":"啊阿","ai":"哀哎唉埃挨暧爱癌皑矮碍艾蔼隘霭","an":"俺安岸按暗案氨胺鞍","ang":"昂盎肮","ao":"傲凹奥懊敖澳熬翱袄","ba":"八叭吧坝巴扒把拔捌爸疤笆罢耙芭跋霸靶","bai":"佰拜摆柏白百稗败","ban":"伴办半扮扳拌搬斑板版班瓣绊般颁","bang":"傍帮梆棒榜磅绑膀蚌谤邦镑","bao":"保包堡宝报抱暴爆胞苞薄褒豹雹饱鲍","bei":"倍北卑备悲惫杯焙狈碑背被贝辈钡","ben":"奔本笨苯","beng":"崩泵甭绷蹦迸","bi":"壁币庇弊彼必敝比毕毖毙痹碧笔臂蓖蔽逼避鄙闭陛鼻","bian":"便卞变扁编贬辨辩辫边遍鞭","biao":"彪标膘表","bie":"别憋瘪鳖","bin":"宾彬摈斌滨濒","bing":"丙兵冰并柄炳病秉饼","bo":"伯剥勃博卜帛拨搏播波渤玻箔脖膊舶菠钵铂驳","bu":"不哺埠布怖捕步簿补部","ca":"擦","cai":"彩才材猜睬菜蔡裁财踩采","can":"参惨惭掺残灿蚕餐","cang":"仓沧舱苍藏","cao":"操曹槽糙草","ce":"侧册厕测策","ceng":"层曾蹭","cha":"叉察岔差插搽查碴茬茶诧","chai":"拆柴豺","chan":"产搀禅缠蝉谗铲阐颤馋","chang":"倡偿厂唱场尝常敞昌猖畅肠长","chao":"吵嘲巢抄朝潮炒超钞","che":"彻扯掣撤澈车","chen":"尘忱晨沉臣衬趁辰郴陈","cheng":"乘呈城惩成承撑橙澄秤称程诚逞骋","chi":"侈吃尺弛持斥池炽痴翅耻赤迟驰齿","chong":"充冲宠崇虫重","chou":"丑仇愁抽畴瞅稠筹绸臭踌酬","chu":"储出初厨处搐楚橱滁畜矗础触躇锄除雏","chuai":"揣","chuan":"串传喘川椽穿船","chuang":"创幢床疮窗闯","chui":"吹垂捶椎炊锤","chun":"唇春椿淳纯蠢醇","chuo":"戳绰","ci":"伺刺慈次此瓷疵磁茨词赐辞雌","cong":"丛从匆囱聪葱","cou":"凑","cu":"促簇粗醋","cuan":"窜篡蹿","cui":"催崔摧淬瘁粹翠脆","cun":"存寸村","cuo":"挫措搓撮磋错","da":"大打搭瘩答达","dai":"代傣呆带待怠戴歹殆袋贷逮","dan":"丹但单惮担掸旦氮淡耽胆蛋诞郸","dang":"党当挡档荡","dao":"倒刀到导岛悼捣盗祷稻蹈道","de":"得德的","deng":"凳灯登瞪等蹬邓","di":"低地堤嫡帝底弟抵敌涤滴狄笛第缔翟蒂迪递","dian":"佃典垫奠巅店惦掂殿淀滇点电甸碘靛颠","diao":"凋刁叼吊掉碉钓雕","die":"叠爹碟蝶谍跌迭","ding":"丁叮定盯订钉锭顶鼎","diu":"丢","dong":"东侗冬冻动恫懂栋洞董","dou":"兜抖斗痘豆逗都陡","du":"堵妒度杜毒渡犊独督睹肚读赌镀","duan":"断段短端缎锻","dui":"兑堆对队","dun":"吨囤墩敦盾蹲遁钝顿","duo":"剁哆垛堕多夺惰掇朵舵跺躲","e":"俄厄娥峨恶扼蛾讹遏鄂额饿鹅","en":"恩","er":"二儿尔洱而耳贰饵","fa":"乏伐发法珐筏罚阀","fan":"凡反帆樊泛烦犯番矾繁翻范藩贩返钒饭","fang":"仿坊妨房放方纺肪芳访防","fei":"匪吠啡废沸肥肺菲诽费非飞","fen":"份分吩坟奋忿愤氛汾焚粉粪纷芬酚","feng":"丰冯凤奉封峰枫烽疯缝蜂讽逢锋风","fou":"否","fu":"付伏佛俘俯傅副咐复夫妇孵富幅府弗扶抚拂敷斧服氟浮涪父甫福符缚肤腐腑腹袱覆讣负赋赴辅辐釜阜附","ga":"嘎噶","gai":"改概溉盖该钙","gan":"干感敢杆柑甘秆竿肝赣赶","gang":"冈刚岗杠港纲缸肛钢","gao":"告搞皋稿篙糕羔膏镐高","ge":"个個割各咯哥戈搁格歌疙胳葛铬阁隔革鸽","gei":"给","gen":"根跟","geng":"埂庚更梗羹耕耿","gong":"供公共功宫工巩弓恭拱攻汞贡躬龚","gou":"勾垢够构沟狗苟购钩","gu":"估古咕固姑孤故沽箍股菇蛊谷辜雇顾骨鼓","gua":"刮剐寡挂瓜褂","guai":"乖怪拐","guan":"关冠官惯棺灌管罐观贯馆","guang":"光广逛","gui":"傀刽圭归柜桂炔瑰癸硅规诡贵跪轨闺鬼龟","gun":"棍滚辊","guo":"国果裹过郭锅","ha":"哈蛤","hai":"亥孩害氦海骇骸","han":"函含喊寒悍憨憾捍撼旱汉汗涵焊罕翰邯酣韩","hang":"夯杭航","hao":"号嚎壕好毫浩耗號豪貉郝","he":"何合呵和喝核河涸盒禾荷菏褐贺赫阂鹤","hei":"嘿黑","hen":"很恨狠痕","heng":"亨哼恒桁横衡","hong":"哄宏弘洪烘红虹轰鸿","hou":"侯候厚后吼喉猴","hu":"乎互呼唬壶弧忽户护沪湖狐琥瑚糊胡葫虎蝴","hua":"划化华哗滑猾画花话","huai":"坏徊怀槐淮","huan":"唤宦幻患换桓欢涣焕环痪缓豢还","huang":"凰幌恍惶慌晃煌皇磺簧荒蝗谎黄","hui":"会卉回徽恢悔惠慧挥晦毁汇灰烩秽绘蛔讳诲贿辉","hun":"婚昏浑混荤魂","huo":"伙惑或活火祸获豁货霍","ji":"伎冀几击剂即及吉圾基妓姬嫉季寂寄己忌急悸技挤既机极棘汲济激畸疾祭积稽箕籍级纪继绩缉肌脊蓟藉计讥记辑迹际集饥鸡","jia":"价佳假加嘉夹嫁家岬架枷甲稼茄荚贾钾颊驾","jian":"件俭健兼减剑剪坚奸尖建拣捡柬检歼涧渐溅煎监硷碱笺简箭缄肩舰艰茧荐见贱践鉴键间饯","jiang":"僵匠奖姜将桨江浆疆蒋讲酱降","jiao":"交侥剿叫娇搅教校椒浇焦狡矫礁窖绞缴胶脚蕉轿较郊酵铰饺骄","jie":"介借劫姐届戒截捷接揭杰洁界疥皆睫秸竭结节芥街解诫阶","jin":"仅今劲尽巾斤晋津浸烬禁筋紧襟谨近进金锦靳","jing":"井京兢净境径惊敬景晶痉睛竞竟粳精经茎荆警镜阱靖静颈鲸","jiong":"炯窘","jiu":"久九厩咎就揪救旧灸玖疚究纠臼舅舊酒韭","ju":"举俱具剧句咀局居巨惧拒拘据桔橘沮炬狙疽矩聚菊距踞锯鞠驹","juan":"倦卷娟捐眷绢鹃","jue":"倔决嚼抉掘撅攫爵绝觉角诀","jun":"俊军君均峻浚竣菌郡钧骏","ka":"卡咖喀","kai":"凯开慨揩楷","kan":"刊勘坎堪槛看瞰砍","kang":"亢康慷扛抗炕糠","kao":"拷烤考靠","ke":"克刻可咳坷壳客柯棵渴磕科苛课颗","ken":"啃垦恳肯","keng":"吭坑","kong":"孔恐控空","kou":"口寇扣抠","ku":"哭库枯窟苦裤酷","kua":"垮夸挎胯跨","kuai":"侩块快筷","kuan":"宽款","kuang":"况匡旷框狂眶矿筐","kui":"亏奎岿愧溃盔窥葵馈魁","kun":"困坤捆昆","kuo":"廓扩括阔","la":"啦喇垃拉腊蜡辣","lai":"来莱赖","lan":"兰婪懒拦揽栏滥澜烂篮缆蓝览谰阑","lang":"廊朗榔浪狼琅郎","lao":"佬劳姥捞涝潦烙牢老酪","le":"乐了肋","lei":"儡勒垒擂泪磊类累蕾镭雷","leng":"冷棱楞","li":"丽例俐傈利力励历厉厘吏哩曆李栗梨沥漓犁狸理璃痢砾礼离立篱粒荔莉里隶鲤黎","lia":"俩","lian":"帘廉怜恋敛涟炼练联脸莲连链镰","liang":"两亮凉晾梁粮粱良谅辆量","liao":"僚寥廖撂撩料燎疗聊辽镣","lie":"冽列劣烈猎裂","lin":"临凛吝拎林淋琳磷粼赁邻霖鳞","ling":"令伶凌另岭灵玲羚菱铃陵零领龄","liu":"六刘柳榴流浏溜琉留瘤硫馏","long":"咙垄拢窿笼聋胧陇隆龙","lou":"娄搂楼漏篓陋","lu":"卢卤庐录戮掳潞炉碌禄芦虏赂路辘陆露颅鲁鹿麓","luan":"乱卵孪峦挛滦","lun":"仑伦抡沦纶论轮","luo":"洛箩络罗萝落螺裸逻锣骆骡","lv":"侣吕屡履律旅氯滤率绿缕虑铝驴","lve":"掠略","ma":"吗嘛妈玛码蚂马骂麻","mai":"买卖埋脉迈霾麦","man":"慢曼满漫瞒蔓蛮谩馒","mang":"忙氓盲芒茫莽","mao":"冒卯帽毛猫矛茂茅貌贸铆锚","me":"么","mei":"妹媒媚寐昧枚梅每没煤玫眉美酶镁霉","men":"们门闷","meng":"孟朦梦檬猛盟萌蒙锰","mi":"密幂弥泌眯秘米糜蜜觅谜谧迷醚靡","mian":"免冕勉娩棉眠绵缅面","miao":"妙庙描渺瞄秒苗藐","mie":"灭蔑","min":"悯抿敏民皿闽","ming":"名命明螟铭鸣","miu":"谬","mo":"墨寞抹摩摸摹末模沫漠磨膜莫蘑陌魔默","mou":"某牟谋","mu":"亩募墓姆幕慕拇暮木母牡牧目睦穆","n":"嗯","na":"呐哪娜拿纳那钠","nai":"乃奈奶氖耐","nan":"南男难","nang":"囊","nao":"恼挠淖脑闹","ne":"呢","nei":"内馁","nen":"嫩","neng":"能","ni":"你倪匿妮尼拟昵泥溺腻逆霓","nian":"年廿念拈捻撵碾蔫辗","niang":"娘酿","niao":"尿鸟","nie":"啮孽捏涅聂镊镍","nin":"您","ning":"凝宁拧柠泞狞","niu":"扭牛纽钮","nong":"农弄浓脓農","nu":"努奴怒","nuan":"暖","nuo":"懦挪糯诺","nv":"女","nve":"疟虐","o":"哦","ou":"偶呕欧殴沤耦藕鸥","pa":"啪帕怕爬琶趴","pai":"徘拍排派湃牌","pan":"判叛攀潘畔盘盼磐","pang":"乓庞旁耪胖","pao":"刨咆抛泡炮袍跑","pei":"佩呸培沛胚裴赔配陪","pen":"喷盆","peng":"彭抨捧朋棚澎烹砰硼碰篷膨蓬鹏","pi":"僻劈匹啤坯屁批披毗琵疲痞癖皮砒脾譬辟霹","pian":"偏片篇骗","piao":"漂瓢票飘","pie":"撇瞥","pin":"品拼聘贫频","ping":"乒凭坪屏平瓶苹萍评","po":"坡婆泊泼珀破粕迫颇魄","pou":"剖","pu":"仆圃埔扑普曝朴浦瀑脯莆菩葡蒲谱铺","qi":"七乞企其凄启器奇契妻岂崎弃戚旗期柒栖棋欺歧气汽沏泣漆畦砌祁祈脐讫起迄骑齐","qia":"恰掐洽","qian":"乾仟前千堑嵌扦欠歉浅潜牵签谦谴迁遣钎钱钳铅黔","qiang":"呛墙强抢枪羌腔蔷","qiao":"乔侨俏峭巧悄撬敲桥橇瞧窍翘锹鞘","qie":"且切怯窃","qin":"亲侵勤寝擒沁琴禽秦芹钦","qing":"倾卿庆情擎晴氢氰清请轻青顷","qiong":"琼穷","qiu":"丘囚求泅球秋邱酋","qu":"区去取娶屈曲渠蛆趋趣躯驱龋","quan":"全券劝圈拳权泉犬痊醛颧","que":"却榷瘸确缺雀鹊","qun":"群裙","ran":"冉染然燃","rang":"嚷壤攘瓤让","rao":"扰绕饶","re":"惹热","ren":"人仁任刃壬妊忍纫认韧","reng":"仍扔","ri":"日","rong":"冗容戎溶熔绒茸荣蓉融","rou":"揉柔肉","ru":"乳儒入如孺汝茹蠕褥辱","ruan":"软阮","rui":"瑞蕊锐","run":"润閏闰","ruo":"弱若","sa":"卅撒洒萨","sai":"塞腮赛鳃","san":"三伞叁散","sang":"丧嗓桑","sao":"嫂扫搔骚","se":"涩瑟色","sen":"森","seng":"僧","sha":"傻刹厦啥杀沙煞砂纱莎","shai":"晒筛","shan":"删善山扇擅杉汕煽珊缮膳苫衫赡闪陕","shang":"上伤商墒尚晌裳赏","shao":"勺哨少捎梢烧稍绍芍邵韶","she":"奢射慑摄涉社舌舍蛇设赊赦","shen":"什伸呻娠婶审慎沈深渗甚申砷神绅肾身","sheng":"剩升圣声牲生甥盛省绳胜","shi":"世事仕使侍势匙十史嗜噬士失始实室尸屎市师式恃拭拾施时是柿氏湿狮矢石示虱蚀视誓识试诗适逝释食饰驶","shou":"兽受售守寿手授收瘦首","shu":"书叔墅孰属庶恕戍抒数暑曙术束枢树梳殊淑漱熟疏竖署舒蔬薯蜀赎输述黍鼠","shua":"刷耍","shuai":"帅摔甩衰","shuan":"拴栓","shuang":"双爽霜","shui":"水睡税谁","shun":"吮瞬舜顺","shuo":"朔烁硕说","si":"丝似司嗣嘶四寺巳思撕斯死私肆饲","song":"宋怂松耸讼诵送颂","sou":"嗽搜擞艘","su":"俗僳塑宿溯粟素肃苏诉速酥","suan":"算蒜酸","sui":"岁碎祟穗绥虽遂隋随隧髓","sun":"孙损笋","suo":"唆所梭琐索缩蓑锁","ta":"他塌塔她它挞榻獭踏蹋","tai":"台太态抬汰泰胎苔酞","tan":"叹坍坛坦弹探摊檀毯滩潭炭痰瘫碳袒谈谭贪","tang":"倘唐堂塘搪棠汤淌烫糖膛趟躺","tao":"套掏桃涛淘滔绦萄讨逃陶","te":"特","teng":"疼腾藤誊","ti":"体剃剔啼嚏屉惕提替梯涕踢蹄锑题","tian":"填天恬添甜田腆舔","tiao":"挑条眺调跳迢","tie":"帖贴铁","ting":"亭停厅听庭廷挺汀烃艇","tong":"同彤捅桐桶痛瞳童筒统通酮铜","tou":"偷头投透骰","tu":"兔凸吐图土屠徒涂秃突途","tuan":"团湍","tui":"推腿蜕褪退颓","tun":"吞屯臀","tuo":"唾坨妥托拓拖椭脱陀驮驼鸵","wa":"哇娃挖洼瓦蛙袜","wai":"外歪","wan":"万丸婉完宛弯惋挽晚湾烷玩皖碗腕豌顽","wang":"亡妄往忘旺望枉汪王网","wei":"为伟伪位卫危味唯喂围委威尉尾巍微惟慰未桅渭潍畏纬维胃苇萎蔚谓违韦魏","wen":"吻文温瘟稳紊纹蚊问闻","weng":"嗡瓮翁","wo":"卧我挝握斡沃涡窝蜗","wu":"乌五伍侮兀务勿午吴吾呜坞屋巫悟戊捂无晤梧武毋污物舞芜诬误钨雾","xi":"习吸喜嘻夕媳希席息悉惜戏昔晰析檄汐洗溪烯熄熙牺犀矽硒稀系细膝袭西铣锡隙","xia":"下侠匣吓夏峡暇狭瞎虾辖霞","xian":"仙先县咸嫌宪弦掀显涎献现纤线羡腺舷衔贤锨闲限险陷馅鲜","xiang":"乡享像厢向响巷想橡湘相祥箱翔襄详象镶项香","xiao":"哮啸嚣孝宵小效晓消淆硝笑肖萧销霄","xie":"些写协卸屑懈挟携斜械楔歇泄泻胁蝎蟹谐谢邪鞋","xin":"信心忻新欣芯薪衅辛锌","xing":"兴刑型姓幸形性惺星杏猩腥行邢醒","xiong":"兄凶匈汹熊胸雄","xiu":"休修嗅朽秀绣羞袖锈","xu":"叙吁嘘墟婿序徐恤戌旭絮绪续蓄虚许酗需须","xuan":"喧宣悬旋暄渲玄癣眩绚轩选","xue":"削学穴薛血雪靴","xun":"勋寻巡循旬殉汛熏薰训讯询迅逊驯","ya":"丫亚压呀哑崖押涯牙芽蚜衙讶轧雅鸦鸭","yan":"严厌咽唁堰奄宴岩延彦掩檐沿淹演炎烟焉焰燕盐眼研砚艳蜒衍言谚阉阎雁颜验","yang":"仰佯养央扬杨样殃氧洋漾疡痒秧羊阳陽鸯","yao":"咬妖姚尧摇瑶窑耀腰舀药要谣遥邀钥","ye":"业也冶叶噎夜掖曳椰液爷耶腋野页","yi":"一义乙亦亿以仪伊依倚医壹夷姨宜屹已异彝役忆意抑揖易椅毅沂溢漪疑疫益矣移绎翌翼肄胰臆艺蚁衣裔议译诣谊逸遗邑铱颐","yin":"印吟因姻寅尹引殷淫茵荫银阴陰隐音饮","ying":"婴应影映樱盈硬缨英荧莹萤营蝇赢迎颖鹰","yo":"哟","yong":"佣勇咏庸恿拥永泳涌用痈臃蛹踊雍","you":"优佑又友右尤幼幽忧悠有油游犹由诱邮酉釉铀","yu":"与予于余俞喻域娱宇寓屿峪御愈愉愚榆欲浴淤渔渝狱玉盂禹羽育舆芋虞裕誉语豫迂逾遇郁阈隅雨预驭鱼","yuan":"元冤原员园圆垣怨愿援渊源猿缘苑袁辕远院鸳","yue":"岳悦曰月粤约越跃阅","yun":"云允匀孕晕耘蕴运郧酝陨韵","za":"匝咋杂砸","zai":"仔再哉在宰栽灾载","zan":"咱攒暂赞","zang":"脏葬赃","zao":"凿噪早枣澡灶燥皂糟藻蚤躁造遭","ze":"则择泽责","zei":"贼","zen":"怎","zeng":"增憎赠","zha":"乍喳扎札柞栅榨渣炸眨诈铡闸","zhai":"债宅寨摘斋窄","zhan":"占展崭战斩栈毡沾湛盏瞻站粘绽蘸詹","zhang":"丈仗帐张彰掌杖樟涨漳瘴章胀账障","zhao":"兆召找招昭沼照爪罩肇赵","zhe":"哲折浙着者蔗蛰辙这遮锗","zhen":"侦帧振斟枕珍甄疹真砧臻诊贞针镇阵震","zheng":"争征怔拯挣政整正狰症睁蒸证郑","zhi":"之侄值制只吱址峙帜志执指挚掷支旨智枝植止殖汁治滞炙痔直知秩稚窒纸织置职肢脂至致芝蜘质趾","zhong":"中仲众忠盅种终肿衷钟","zhou":"周咒宙州帚昼洲皱粥肘舟诌轴骤","zhu":"主住助嘱拄朱柱株注烛煮猪珠瞩祝竹筑著蛀蛛诛诸贮逐铸驻","zhua":"抓","zhuai":"拽","zhuan":"专撰砖篆赚转","zhuang":"壮妆庄撞桩状装","zhui":"坠缀赘追锥","zhun":"准谆","zhuo":"卓啄拙捉桌浊灼茁酌","zi":"兹咨姿子字孜淄渍滋滓籽紫自资","zong":"宗总棕纵综踪鬃","zou":"奏揍走邹","zu":"卒族祖租组诅足阻","zuan":"纂钻","zui":"嘴最罪醉","zun":"尊遵","zuo":"佐作做坐左座昨琢"};
  const POLYPHONES = {
    "角": ["jue", "jiao"],
    "重": ["chong", "zhong"],
    "调": ["tiao", "diao"],
    "行": ["xing", "hang"],
    "长": ["chang", "zhang"],
    "还": ["huan", "hai"],
    "传": ["chuan", "zhuan"],
    "觉": ["jue", "jiao"],
    "率": ["lv", "shuai"],
    "乐": ["le", "yue"],
    "参": ["can", "shen"],
    "弹": ["tan", "dan"],
    "露": ["lu", "lou"],
    "藏": ["cang", "zang"],
    "省": ["sheng", "xing"],
    "降": ["jiang", "xiang"],
    "校": ["jiao", "xiao"],
    "称": ["cheng", "chen"],
    "数": ["shu", "shuo"],
    "读": ["du", "dou"],
    "便": ["bian", "pian"],
    "宿": ["su", "xiu"],
    "朝": ["chao", "zhao"],
    "差": ["cha", "chai"],
    "强": ["qiang", "jiang"],
    "系": ["xi", "ji"],
    "落": ["luo", "la"],
    "解": ["jie", "xie"],
    "和": ["he", "huo"],
    "会": ["hui", "kuai"],
    "模": ["mo", "mu"],
    "着": ["zhe", "zhao"],
    "得": ["de", "dei"],
    "地": ["di", "de"],
    "应": ["ying", "ying"],
    "当": ["dang", "dang"],
    "间": ["jian", "jian"],
    "度": ["du", "duo"],
  };

  let charToPinyin = null;
  function getCharMap() {
    if (charToPinyin) return charToPinyin;
    charToPinyin = Object.create(null);
    for (const [py, chars] of Object.entries(PINYIN_BY_SYLLABLE)) {
      for (let i = 0; i < chars.length; i += 1) {
        charToPinyin[chars[i]] = py;
      }
    }
    return charToPinyin;
  }

  const HAS_CJK = /[\u3400-\u9fff]/;
  const IS_CJK_CHAR = ch => {
    const code = ch.charCodeAt(0);
    return code >= 0x3400 && code <= 0x9fff;
  };
  const PURE_ASCII_LETTERS = /^[a-z]{2,}$/;

  function escapeHTML(value) {
    return String(value ?? "").replace(/[&<>"']/g, ch => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  function escapePattern(value) {
    return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  }

  function normalize(text) {
    return String(text ?? "").normalize("NFKC").toLocaleLowerCase();
  }

  function tokenize(query) {
    return normalize(query).trim().split(/[\s,，;；、\/|›>]+/).filter(Boolean);
  }

  /* Extract CJK runs and their 2-grams for soft unspaced Chinese recall. */
  function cjkRuns(text) {
    const matches = normalize(text).match(/[\u3400-\u9fff]+/g);
    return matches || [];
  }

  function cjkNgrams(text) {
    const grams = [];
    const seen = new Set();
    for (const run of cjkRuns(text)) {
      if (run.length === 1) {
        if (!seen.has(run)) { seen.add(run); grams.push(run); }
        continue;
      }
      for (let i = 0; i < run.length - 1; i += 1) {
        const g = run.slice(i, i + 2);
        if (!seen.has(g)) { seen.add(g); grams.push(g); }
      }
    }
    return grams;
  }

  /* Convert text to syllable-aligned full pinyin & initials (with polyphone alternates). */
  function toPinyin(text) {
    const map = getCharMap();
    const norm = normalize(text);
    const syllables = [];
    const altSyllables = [];
    let hasAlt = false;
    let asciiBuf = "";
    const flushAscii = () => {
      if (!asciiBuf) return;
      syllables.push(asciiBuf);
      altSyllables.push(asciiBuf);
      asciiBuf = "";
    };
    for (let i = 0; i < norm.length; i += 1) {
      const ch = norm[i];
      if ((ch >= "a" && ch <= "z") || (ch >= "0" && ch <= "9")) {
        asciiBuf += ch;
        continue;
      }
      flushAscii();
      if (IS_CJK_CHAR(ch)) {
        const primary = map[ch];
        if (primary) {
          syllables.push(primary);
          const poly = POLYPHONES[ch];
          if (poly && poly.length > 1 && poly[1] !== primary) {
            altSyllables.push(poly[1]);
            hasAlt = true;
          } else {
            altSyllables.push(primary);
          }
        }
      }
    }
    flushAscii();
    const buildMeta = list => {
      const starts = [];
      let pos = 0;
      let initials = "";
      for (let i = 0; i < list.length; i += 1) {
        starts.push(pos);
        pos += list[i].length;
        initials += list[i][0] || "";
      }
      return { syllables: list, joined: list.join(""), initials, starts };
    };
    const primary = buildMeta(syllables);
    const alt = hasAlt ? buildMeta(altSyllables) : null;
    return { ...primary, alt };
  }

  /* Match pure-ASCII pinyin query against a compiled pinyin metadata object.
   * Syllable-aligned prefix/full matching avoids noisy mid-syllable false positives. */
  function matchPinyinMeta(meta, query) {
    if (!meta || !meta.joined || !query) return 0;
    const scoreSingle = m => {
      if (!m || !m.joined) return 0;
      if (m.joined === query) return 1.0;
      if (m.initials === query) return 0.96;
      if (m.joined.startsWith(query)) {
        /* Prefer full syllable boundary or prefix of a syllable starting at 0 */
        return 0.88;
      }
      if (m.initials.startsWith(query)) return 0.84;
      const initIdx = m.initials.indexOf(query);
      if (initIdx >= 0) return 0.72;
      /* Check syllable-boundary aligned match inside joined pinyin */
      for (let i = 0; i < m.starts.length; i += 1) {
        const start = m.starts[i];
        if (m.joined.startsWith(query, start)) {
          return start === 0 ? 0.88 : 0.76;
        }
      }
      /* Mixed syllable + next-initials shorthand (e.g. "beiyongmx" -> bei+yong+m+x) */
      if (query.length >= 3 && m.syllables.length >= 2) {
        for (let startIdx = 0; startIdx < m.syllables.length; startIdx += 1) {
          let qi = 0;
          let matched = 0;
          let ok = true;
          for (let si = startIdx; si < m.syllables.length && qi < query.length; si += 1) {
            const syl = m.syllables[si];
            if (query.startsWith(syl, qi)) {
              qi += syl.length;
              matched += 1;
            } else if (syl.startsWith(query.slice(qi))) {
              qi = query.length;
              matched += 1;
            } else if (query[qi] === syl[0]) {
              qi += 1;
              matched += 1;
            } else {
              ok = false;
              break;
            }
          }
          if (ok && qi === query.length && matched >= 2) {
            return startIdx === 0 ? 0.8 : 0.68;
          }
        }
      }
      return 0;
    };
    let best = scoreSingle(meta);
    if (meta.alt) best = Math.max(best, scoreSingle(meta.alt));
    return best;
  }

  /* Bidirectional synonym / legacy-name dictionary. */
  const SYNONYM_GROUPS = [
    ["陪伴通用模型", "共享启动模型", "插件启动模型", "全局默认模型", "全局默认", "插件任务模型 provider", "插件任务模型", "通用模型", "默认模型", "初始主 provider", "model.provider_id", "provider_id"],
    ["备用模型", "回退模型", "全局备用模型", "回复任务的回退 provider", "回退 provider", "fallback", "fallback_provider_id", "model.fallback_provider_id", "替补模型"],
    ["免打扰", "防打扰", "安静时段", "防骚扰", "静默时段", "勿扰", "quiet"],
    ["递话", "转告", "传话", "跨窗口递话", "跨群递话", "relay"],
    ["生图", "画图", "图像生成", "文生图", "出图", "配图", "画画", "comfyui", "sd webui"],
    ["戳一戳", "拍一拍", "poke"],
    ["语音", "朗读", "tts", "发音人", "音色", "配音", "voice"],
    ["日记", "手账", "生活记录", "diary", "journal"],
    ["睡眠", "作息", "入睡", "熬夜", "休息", "sleep", "rest"],
    ["qq空间", "qq 空间", "空间", "说说", "好友动态", "qzone"],
    ["记忆", "事实", "经历", "回忆", "memory"],
    ["复盘", "反思", "自我复盘", "每日复盘", "self_review"],
    ["衣柜", "穿搭", "换装", "参考图", "wardrobe", "outfit"],
    ["屏幕", "截屏", "截图", "看屏", "屏幕解读", "screen"],
    ["摄像头", "相机", "拍照", "camera"],
    ["米家", "家居", "智能家居", "家电", "mihome"],
    ["手机", "安卓", "adb", "移动端", "手机端", "phone", "mobile"],
    ["华为健康", "运动健康", "心率", "步数", "huawei", "health"],
    ["天气", "气温", "预报", "和风", "weather"],
    ["历法", "节气", "黄历", "日历", "农历", "节假日", "calendar", "solar"],
    ["余额", "费用", "计价", "汇率", "预算", "单价", "成本", "token", "budget", "pricing", "额度看板", "多站点", "中转站", "quota"],
    ["主站", "指定主站", "主站点", "切换主站", "默认站点", "余额感知主站", "primary_station", "primary_station_id"],
    ["备份", "还原", "恢复", "快照", "backup", "restore"],
    ["迁移", "旧版", "v1", "迁入", "legacy", "migration"],
    ["导入导出", "导出配置", "导入配置", "配置快照", "config port"],
    ["主题", "外观", "深色模式", "暗色", "字号", "圆角", "密度", "动效", "theme", "appearance"],
    ["游戏", "棋桌", "象棋", "五子棋", "你画我猜", "海龟汤", "21点", "game"],
    ["直播", "live2d", "vts", "vtube", "obs", "弹幕", "live"],
    ["一起房间", "共处电话", "一起看", "一起读", "小说", "together"],
    ["现实触及", "叫醒", "闹钟", "reality"],
    ["群聊", "插话", "复读", "接话", "旁听", "话题线", "interjection"],
    ["性能模式", "低配", "低配机", "省内存", "精简模式", "轻量模式", "性能调优", "性能档位", "兼容稳妥", "机械硬盘", "hdd", "nas", "卡顿", "防卡顿", "流畅", "performance", "tuning"],
    ["分段", "拆句", "多条发送", "segmentation", "segment"],
    ["表情", "表情包", "贴纸", "reaction", "emoji"],
    ["联网", "搜索服务", "网页搜索", "deepseek"],
    ["人设", "人格", "角色档案", "角色设定", "persona", "role_profile"],
    ["私聊工具", "工具档位", "工具组", "private_tool_profile", "private_tool_groups"],
    ["拒答", "替换规则", "敏感词", "审查", "refusal", "replacement"],
    ["合并转发", "转发消息", "forward", "forward_intake"],
    ["撤回", "防撤回", "recall"],
    ["存储", "清理", "缓存", "磁盘", "storage"],
    ["数据库瘦身", "数据库体检", "一键瘦身", "瘦身", "体检", "压缩", "释放空间", "空闲页", "vacuum", "去重", "冷归档", "compact"],
    ["连库数", "查库通道", "连接池", "内存映射", "mmap", "写盘策略", "synchronous", "wal", "写入合并", "后台并发", "语音并发"],
  ];

  /* Precompile synonym map & pinyin for synonym phrases. */
  const SYNONYM_INDEX = SYNONYM_GROUPS.map(group => {
    const normalized = group.map(item => normalize(item).trim()).filter(Boolean);
    const entries = normalized.map((phrase, idx) => ({
      raw: group[idx],
      phrase,
      pinyin: HAS_CJK.test(phrase) ? toPinyin(phrase) : null,
    }));
    return { entries };
  });

  const MULTIWORD_SYNONYM_PHRASES = (() => {
    const set = new Set();
    for (const group of SYNONYM_INDEX) {
      for (const item of group.entries) {
        if (item.phrase.includes(" ")) set.add(item.phrase);
      }
    }
    return [...set].map(phrase => ({ phrase, parts: phrase.split(/\s+/) }))
      .sort((a, b) => b.parts.length - a.parts.length || b.phrase.length - a.phrase.length);
  })();

  function expandExactAliases(rawPhrase) {
    const phrase = normalize(rawPhrase).trim();
    if (!phrase) return [];
    const results = [];
    const seen = new Set([phrase]);
    for (const group of SYNONYM_INDEX) {
      if (!group.entries.some(item => item.phrase === phrase)) continue;
      for (const item of group.entries) {
        if (seen.has(item.phrase)) continue;
        seen.add(item.phrase);
        results.push({ phrase: item.phrase, label: item.raw, mode: "exact" });
      }
    }
    return results;
  }

  function coalesceKnownPhrases(terms) {
    if (!Array.isArray(terms) || terms.length <= 1 || !MULTIWORD_SYNONYM_PHRASES.length) return terms;
    const merged = [];
    let i = 0;
    while (i < terms.length) {
      let matched = null;
      for (const candidate of MULTIWORD_SYNONYM_PHRASES) {
        const len = candidate.parts.length;
        if (i + len > terms.length) continue;
        let ok = true;
        for (let j = 0; j < len; j += 1) {
          if (terms[i + j] !== candidate.parts[j]) { ok = false; break; }
        }
        if (ok) { matched = candidate; break; }
      }
      if (matched) {
        merged.push(matched.phrase);
        i += matched.parts.length;
      } else {
        merged.push(terms[i]);
        i += 1;
      }
    }
    return merged;
  }

  function expandAliases(rawTerm) {
    const term = normalize(rawTerm).trim();
    if (!term) return [];
    const results = [];
    const seen = new Set([term]);
    const isAsciiPinyin = PURE_ASCII_LETTERS.test(term);
    for (const group of SYNONYM_INDEX) {
      let matched = false;
      let matchMode = "";
      for (const item of group.entries) {
        if (item.phrase === term) {
          matched = true; matchMode = "exact"; break;
        }
        if (term.length >= 2 && item.phrase.length >= 2 && (item.phrase.includes(term) || term.includes(item.phrase))) {
          /* Only trigger substring expansion when substantial overlap */
          const shorter = Math.min(term.length, item.phrase.length);
          const longer = Math.max(term.length, item.phrase.length);
          if (shorter >= 2 && (shorter / longer >= 0.5 || (HAS_CJK.test(term) && term.length >= 2 && item.phrase.includes(term)))) {
            matched = true; matchMode = "sub"; break;
          }
        }
        if (isAsciiPinyin && item.pinyin && matchPinyinMeta(item.pinyin, term) >= 0.84) {
          matched = true; matchMode = "pinyin"; break;
        }
      }
      if (!matched) continue;
      for (const item of group.entries) {
        if (seen.has(item.phrase)) continue;
        seen.add(item.phrase);
        results.push({ phrase: item.phrase, label: item.raw, mode: matchMode });
      }
    }
    return results;
  }

  /* Exact Damerau-Levenshtein <= 1 check in O(len) time, zero allocations. */
  function editDistanceAtMostOne(a, b) {
    const la = a.length, lb = b.length;
    if (Math.abs(la - lb) > 1) return false;
    if (a === b) return true;
    let i = 0, j = 0, edits = 0;
    while (i < la && j < lb) {
      if (a[i] === b[j]) { i += 1; j += 1; continue; }
      edits += 1;
      if (edits > 1) return false;
      /* Check adjacent transposition (swap) */
      if (i + 1 < la && j + 1 < lb && a[i] === b[j + 1] && a[i + 1] === b[j]) {
        i += 2; j += 2; continue;
      }
      if (la > lb) i += 1;
      else if (lb > la) j += 1;
      else { i += 1; j += 1; }
    }
    if (i < la || j < lb) edits += 1;
    return edits <= 1;
  }

  /* Check if needle matches any sliding window of haystack within edit distance <= 1.
   * Returns: 0 = no match, 1 = generic 1-edit match, 2 = homophone-assisted 1-edit match. */
  function fuzzyWindowMatch(needle, haystack) {
    if (!needle || !haystack) return 0;
    const nLen = needle.length;
    const hLen = haystack.length;
    if (hLen < nLen - 1) return 0;
    const isCjk = HAS_CJK.test(needle);
    if (isCjk && nLen < 2) return 0;
    if (!isCjk && nLen < 4) return 0;

    /* For 2-char CJK typo (e.g. "递画" vs "递话", "倍用" vs "备用"):
     * require edit distance 1 AND same pinyin on the swapped char (homophone typo)
     * so we catch real Chinese IME typos without noisy false positives. */
    if (isCjk && nLen === 2) {
      const map = getCharMap();
      const nPy0 = map[needle[0]] || "";
      const nPy1 = map[needle[1]] || "";
      for (let i = 0; i <= hLen - 2; i += 1) {
        const sub = haystack.slice(i, i + 2);
        if (sub === needle) return 2;
        if (sub[0] === needle[0] && nPy1 && map[sub[1]] === nPy1) return 2;
        if (sub[1] === needle[1] && nPy0 && map[sub[0]] === nPy0) return 2;
      }
      return 0;
    }

    /* Fast O(needle) missing-char prefilter: if >=2 chars of needle are absent from haystack,
     * Damerau-Levenshtein distance is guaranteed >= 2, skipping 99%+ of non-matches instantly. */
    let missing = 0;
    for (let k = 0; k < nLen; k += 1) {
      if (haystack.indexOf(needle[k]) < 0) {
        missing += 1;
        if (missing > 1) return 0;
      }
    }

    const map = isCjk ? getCharMap() : null;
    let best = 0;
    for (let winLen = Math.max(2, nLen - 1); winLen <= Math.min(hLen, nLen + 1); winLen += 1) {
      for (let i = 0; i <= hLen - winLen; i += 1) {
        const sub = haystack.slice(i, i + winLen);
        if (!editDistanceAtMostOne(needle, sub)) continue;
        if (isCjk && winLen === nLen && map) {
          /* Check if the single substituted char shares pinyin (homophone IME typo) */
          let diffIdx = -1;
          for (let p = 0; p < nLen; p += 1) {
            if (needle[p] !== sub[p]) { diffIdx = p; break; }
          }
          if (diffIdx >= 0 && map[needle[diffIdx]] && map[needle[diffIdx]] === map[sub[diffIdx]]) {
            return 2;
          }
        }
        best = 1;
      }
    }
    return best;
  }

  /* Compact ordered subsequence check (e.g. "陪伴模型" inside "陪伴通用模型"). */
  function orderedSubsequenceScore(needle, haystack) {
    if (!needle || !haystack || needle.length < 2 || needle.length >= haystack.length) return 0;
    let first = -1, last = -1, ni = 0;
    for (let hi = 0; hi < haystack.length && ni < needle.length; hi += 1) {
      if (haystack[hi] === needle[ni]) {
        if (first < 0) first = hi;
        last = hi;
        ni += 1;
      }
    }
    if (ni < needle.length) return 0;
    const span = last - first + 1;
    const maxSpan = needle.length + Math.max(3, Math.ceil(needle.length * 0.75));
    if (span > maxSpan) return 0;
    return Math.max(0.45, Math.min(0.85, needle.length / span));
  }

  function directScore(haystack, needle, weights) {
    if (!haystack || !needle) return 0;
    if (haystack === needle) return weights[0];
    if (haystack.startsWith(needle)) return weights[1];
    if (haystack.includes(needle)) return weights[2];
    return 0;
  }

  /* Precompile an array of arbitrary items into fast searchable entries. */
  function prepare(items, options = {}) {
    const getTitle = options.getTitle || (item => item.title || item.label || "");
    const getDesc = options.getDesc || (item => item.desc || item.help || item.description || "");
    const getKey = options.getKey || (item => item.key || item.field || "");
    const getAliases = options.getAliases || (item => item.aliases || []);
    const getGroup = options.getGroup || (item => item.group || item.sectionTitle || item.groupTitle || "");
    const getBreadcrumb = options.getBreadcrumb || (item => item.breadcrumb || "");

    return (Array.isArray(items) ? items : []).map((item, order) => {
      const rawTitle = String(getTitle(item) ?? "");
      const rawDesc = String(getDesc(item) ?? "");
      const rawKey = String(getKey(item) ?? "");
      const rawGroup = String(getGroup(item) ?? "");
      const rawBreadcrumb = String(getBreadcrumb(item) ?? "");
      const rawAliases = (Array.isArray(getAliases(item)) ? getAliases(item) : [])
        .map(a => String(a ?? "").trim())
        .filter(Boolean);

      const title = normalize(rawTitle);
      const desc = normalize(rawDesc);
      const key = normalize(rawKey);
      const group = normalize(rawGroup);
      const breadcrumb = normalize(rawBreadcrumb);
      const aliases = rawAliases.map(a => ({ raw: a, norm: normalize(a), pinyin: HAS_CJK.test(a) ? toPinyin(a) : null }));
      const aliasJoined = aliases.map(a => a.norm).join(" ");
      const combined = `${title}\n${key}\n${aliasJoined}\n${group}\n${breadcrumb}\n${desc}`;

      return {
        item,
        order,
        rawTitle,
        rawDesc,
        rawKey,
        rawGroup,
        rawBreadcrumb,
        title,
        desc,
        key,
        group,
        breadcrumb,
        aliases,
        combined,
        titlePinyin: HAS_CJK.test(title) ? toPinyin(title) : null,
        groupPinyin: HAS_CJK.test(group) ? toPinyin(group) : null,
        descPinyin: HAS_CJK.test(desc) && desc.length <= 80 ? toPinyin(desc) : null,
      };
    });
  }

  function parseQuery(rawQuery) {
    const normalized = normalize(rawQuery).trim();
    const rawTerms = tokenize(rawQuery);
    const terms = coalesceKnownPhrases(rawTerms);
    const expandedByTerm = terms.map(term => ({
      term,
      isAsciiPinyin: PURE_ASCII_LETTERS.test(term),
      isCjk: HAS_CJK.test(term),
      ngrams: HAS_CJK.test(term) && !term.includes(" ") && term.length >= 3 ? cjkNgrams(term) : [],
      synonyms: term.includes(" ") ? expandExactAliases(term) : expandAliases(term),
    }));
    /* Whole-query synonym shortcut ONLY when the entire query is an exact
     * multi-word synonym phrase (e.g. "qq 空间", "sd webui"), never substring-
     * matching longer multi-term queries like "qq 空间 评论". */
    const wholeSynonyms = rawTerms.length > 1 ? expandExactAliases(normalized) : [];
    return { raw: String(rawQuery ?? ""), normalized, terms, expandedByTerm, wholeSynonyms };
  }

  function scoreOneTerm(entry, termInfo) {
    const { term, isAsciiPinyin, isCjk, ngrams, synonyms } = termInfo;
    let best = 0;
    let matchKind = "";
    let reason = "";
    const matchedTerms = [];

    /* Layer 1: Direct exact / prefix / substring (fast combined pre-check) */
    if (entry.combined.includes(term)) {
      const sTitle = directScore(entry.title, term, [128, 104, 78]);
      const sKey = directScore(entry.key, term, [90, 72, 54]);
      const sGroup = directScore(entry.group, term, [62, 48, 36]);
      const sBread = directScore(entry.breadcrumb, term, [56, 44, 32]);
      const sDesc = directScore(entry.desc, term, [24, 19, 14]);
      let sAlias = 0;
      let hitAliasRaw = "";
      for (const al of entry.aliases) {
        const sc = directScore(al.norm, term, [92, 72, 52]);
        if (sc > sAlias) { sAlias = sc; hitAliasRaw = al.raw; }
      }
      const directBest = Math.max(sTitle, sKey, sGroup, sBread, sDesc, sAlias);
      if (directBest > 0) {
        best = directBest;
        matchedTerms.push(term);
        if (sAlias > 0 && sTitle === 0 && sKey === 0 && sAlias >= sGroup) {
          matchKind = "alias";
          reason = `别名：${hitAliasRaw}`;
        } else {
          matchKind = sTitle === 128 || sKey === 90 ? "exact" : "direct";
        }
      }
    }

    /* Layer 4 (early for pure ASCII pinyin): Pinyin full & initials directly on item */
    if (best < 72 && isAsciiPinyin) {
      let pyBest = 0;
      let pyReason = "拼音匹配";
      if (entry.titlePinyin) {
        const r = matchPinyinMeta(entry.titlePinyin, term);
        if (r > 0) pyBest = Math.max(pyBest, Math.round(r * 82));
      }
      for (const al of entry.aliases) {
        if (al.pinyin) {
          const r = matchPinyinMeta(al.pinyin, term);
          if (r > 0) {
            const sc = Math.round(r * 72);
            if (sc > pyBest) { pyBest = sc; pyReason = `拼音：${al.raw}`; }
          }
        }
      }
      if (entry.groupPinyin) {
        const r = matchPinyinMeta(entry.groupPinyin, term);
        if (r > 0) pyBest = Math.max(pyBest, Math.round(r * 48));
      }
      if (entry.descPinyin && term.length >= 3) {
        const r = matchPinyinMeta(entry.descPinyin, term);
        if (r >= 0.72) pyBest = Math.max(pyBest, Math.round(r * 26));
      }
      if (pyBest > best) {
        best = pyBest;
        matchKind = "pinyin";
        reason = pyReason;
      }
    }

    /* Layer 2: Bidirectional synonyms (fast combined pre-check per synonym) */
    if (best < 90 && synonyms.length) {
      for (const syn of synonyms) {
        if (!entry.combined.includes(syn.phrase)) continue;
        const factor = syn.mode === "exact" ? 0.88 : syn.mode === "pinyin" ? 0.78 : 0.76;
        const scT = directScore(entry.title, syn.phrase, [104, 82, 64]) * factor;
        const scK = directScore(entry.key, syn.phrase, [76, 60, 46]) * factor;
        const scG = directScore(entry.group, syn.phrase, [54, 42, 30]) * factor;
        const scD = directScore(entry.desc, syn.phrase, [22, 18, 13]) * factor;
        let scA = 0;
        let aliasLabel = syn.label;
        for (const al of entry.aliases) {
          const sa = directScore(al.norm, syn.phrase, [84, 66, 48]) * factor;
          if (sa > scA) { scA = sa; aliasLabel = al.raw; }
        }
        const synBest = Math.round(Math.max(scT, scK, scG, scD, scA));
        if (synBest > best) {
          best = synBest;
          matchKind = syn.mode === "pinyin" ? "pinyin" : "synonym";
          reason = syn.mode === "pinyin" ? `拼音：${aliasLabel}` : `别名：${aliasLabel}`;
          matchedTerms.push(syn.phrase);
        }
      }
    }

    /* Layer 5a: 1-char typo tolerance (homophone-assisted outranks generic 1-edit so "倍用模型" ranks "备用模型" #1;
     * never overwrite a direct title/key/alias match with a misleading "容错" badge) */
    if (best < 40) {
      let typoScore = 0;
      let typoReason = "容错匹配";
      const titleFuzzy = fuzzyWindowMatch(term, entry.title);
      if (titleFuzzy) {
        typoScore = titleFuzzy === 2 ? 78 : 68;
      } else {
        for (const al of entry.aliases) {
          const aliasFuzzy = fuzzyWindowMatch(term, al.norm);
          if (aliasFuzzy) {
            const sc = aliasFuzzy === 2 ? 72 : 62;
            if (sc > typoScore) { typoScore = sc; typoReason = `容错：${al.raw}`; }
          }
        }
      }
      if (!typoScore && !isCjk && term.length >= 4 && fuzzyWindowMatch(term, entry.key)) {
        typoScore = 52;
      }
      if (!typoScore && isCjk && term.length >= 3 && fuzzyWindowMatch(term, entry.group)) {
        typoScore = 44;
      }
      if (typoScore > best) {
        best = typoScore;
        matchKind = "fuzzy";
        reason = typoReason;
      }
    }

    /* Layer 3: Unspaced Chinese 2-gram soft recall */
    if (best === 0 && isCjk && ngrams.length >= 2) {
      const matchedGrams = [];
      let titleHits = 0;
      let otherHits = 0;
      for (const g of ngrams) {
        if (!entry.combined.includes(g)) continue;
        if (entry.title.includes(g) || entry.aliases.some(a => a.norm.includes(g))) {
          titleHits += 1;
          matchedGrams.push(g);
        } else {
          otherHits += 1;
          matchedGrams.push(g);
        }
      }
      const totalHits = titleHits + otherHits;
      const coverage = totalHits / ngrams.length;
      if (totalHits >= 2 && coverage >= 0.45) {
        const ngramScore = Math.round((titleHits * 22 + otherHits * 9) * (0.6 + 0.5 * coverage));
        if (ngramScore > best) {
          best = Math.min(58, ngramScore);
          matchKind = "ngram";
          reason = "分词匹配";
          for (const g of matchedGrams) matchedTerms.push(g);
        }
      }
    }

    /* Layer 5b: Compact ordered subsequence (e.g. "陪伴模型" inside "陪伴通用模型") */
    if (best === 0 && isCjk && term.length >= 2) {
      let subScore = 0;
      let subReason = "近似匹配";
      const subTitle = orderedSubsequenceScore(term, entry.title);
      if (subTitle > 0) {
        subScore = Math.round(subTitle * 54);
        for (const ch of term) if (IS_CJK_CHAR(ch)) matchedTerms.push(ch);
      } else {
        for (const al of entry.aliases) {
          const subAlias = orderedSubsequenceScore(term, al.norm);
          if (subAlias > 0) {
            subScore = Math.round(subAlias * 46);
            subReason = `别名：${al.raw}`;
            break;
          }
        }
      }
      if (subScore > best) {
        best = subScore;
        matchKind = "fuzzy";
        reason = subReason;
      }
    }

    return { score: best, matchKind, reason, matchedTerms };
  }

  function scoreEntry(entry, parsedQuery, options = {}) {
    if (!parsedQuery.terms.length) {
      return { score: 0, matchKind: "empty", reason: "", matchedTerms: [] };
    }
    let total = 0;
    let primaryKind = "";
    let primaryReason = "";
    const matchedTerms = [];
    const kindPriority = { exact: 5, direct: 4, alias: 3, synonym: 3, pinyin: 2, ngram: 2, fuzzy: 1 };

    for (const termInfo of parsedQuery.expandedByTerm) {
      const res = scoreOneTerm(entry, termInfo);
      if (res.score <= 0) return { score: -1, matchKind: "", reason: "", matchedTerms: [] };
      total += res.score;
      for (const mt of res.matchedTerms) {
        if (mt && !matchedTerms.includes(mt)) matchedTerms.push(mt);
      }
      if (!primaryKind || (res.reason && !primaryReason)
          || (kindPriority[res.matchKind] || 0) < (kindPriority[primaryKind] || 99)) {
        if (res.reason) { primaryKind = res.matchKind; primaryReason = res.reason; }
        else if (!primaryKind) { primaryKind = res.matchKind; }
      }
    }

    /* Exact full-title bonus & current-tab contextual boost */
    if (entry.title === parsedQuery.normalized) total += 32;
    else if (entry.aliases.some(al => al.norm === parsedQuery.normalized)) total += 24;
    if (typeof options.bonus === "function") total += Number(options.bonus(entry.item)) || 0;

    return { score: total, matchKind: primaryKind || "direct", reason: primaryReason, matchedTerms };
  }

  function search(index, rawQuery, options = {}) {
    const parsed = parseQuery(rawQuery);
    const list = Array.isArray(index) ? index : [];
    if (!parsed.terms.length) {
      return list.map(entry => ({
        item: entry.item,
        score: 0,
        order: entry.order,
        matchKind: "empty",
        reason: "",
        matchedTerms: [],
      }));
    }
    const hits = [];
    for (let i = 0; i < list.length; i += 1) {
      const entry = list[i];
      const scored = scoreEntry(entry, parsed, options);
      if (scored.score > 0) {
        hits.push({
          item: entry.item,
          score: scored.score,
          order: entry.order,
          matchKind: scored.matchKind,
          reason: scored.reason,
          matchedTerms: scored.matchedTerms,
        });
      }
    }
    hits.sort((a, b) => b.score - a.score || a.order - b.order);
    const limit = Number(options.limit);
    return Number.isInteger(limit) && limit > 0 ? hits.slice(0, limit) : hits;
  }

  /* XSS-safe <mark> highlighter preserving original text case and literal regex chars. */
  function highlight(value, matchedTerms, escapeFn = escapeHTML) {
    const text = String(value ?? "");
    const rawTerms = (Array.isArray(matchedTerms) ? matchedTerms : [matchedTerms])
      .map(t => String(t ?? "").trim())
      .filter(Boolean);
    if (!rawTerms.length) return escapeFn(text);
    /* Sort longer terms first so multi-char matches take precedence over single-char fragments */
    const unique = [...new Set(rawTerms)].sort((a, b) => b.length - a.length);
    const pattern = new RegExp(`(${unique.map(escapePattern).join("|")})`, "gi");
    return text.split(pattern)
      .map((part, idx) => (idx % 2 ? `<mark>${escapeFn(part)}</mark>` : escapeFn(part)))
      .join("");
  }

  /* =========================================================================
   * CompanionSearchRegistry: unified auto-coverage registry + backend catalog
   * ========================================================================= */
  const registrySources = new Map();
  const registryListeners = new Set();
  let backendCatalog = null;

  function notifyRegistryChange() {
    for (const listener of registryListeners) {
      try { listener(); } catch (_) { /* isolate listener failure */ }
    }
  }

  function registerSource(sourceId, providerFn) {
    if (!sourceId || typeof providerFn !== "function") return false;
    registrySources.set(String(sourceId), providerFn);
    notifyRegistryChange();
    return true;
  }

  function unregisterSource(sourceId) {
    const removed = registrySources.delete(String(sourceId));
    if (removed) notifyRegistryChange();
    return removed;
  }

  function onRegistryChange(listener) {
    if (typeof listener !== "function") return () => {};
    registryListeners.add(listener);
    return () => registryListeners.delete(listener);
  }

  /* Map backend _conf_schema.json section prefixes to their best in-cottage target. */
  function pluginConfigLocation(item) {
    const path = String(item.path || "");
    const section = String(item.section || "");
    const field = String(item.field || "");
    if (path === "model.provider_id") {
      return { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#shared-model-provider" } };
    }
    if (path === "model.fallback_provider_id") {
      return { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#shared-model-fallback" } };
    }
    if (section === "model") {
      if (field.includes("microusd")) return { next: "manage", tab: "models", modelsTab: "pricing", root: "#modelWorkspace" };
      return { next: "manage", tab: "models", modelsTab: "fn", root: "#modelWorkspace" };
    }
    if (section === "segmentation") {
      return { next: "manage", tab: "expression", subtab: "segment", expressionModule: "expression-text", root: "#replyWorkspace" };
    }
    if (section === "voice_reply") {
      if (field.endsWith("_provider_id") || field === "provider_id") {
        return { next: "manage", tab: "models", modelsTab: "voice", root: "#modelWorkspace" };
      }
      return { next: "manage", tab: "expression", subtab: "voice", expressionModule: "expression-voice", root: "#voiceReplyPolicyWorkspace" };
    }
    if (section === "poke") {
      return { next: "manage", tab: "expression", subtab: "platform", expressionModule: "expression-platform", root: "#pokePolicyWorkspace" };
    }
    if (section === "runtime") {
      if (field.includes("budget") || field.includes("soft_limit") || field.includes("soft_deferred")) {
        return { next: "manage", tab: "models", modelsTab: "fn", root: "#budgetConfigWorkspace" };
      }
      if (field.startsWith("forward_")) {
        return { next: "manage", tab: "understanding", understandingTab: "forwards", root: "#forwardIntakeWorkspace" };
      }
      return { next: "manage", tab: "maintenance" };
    }
    if (section === "storage") {
      if (field.includes("backup") || field.includes("retention")) {
        return { next: "manage", tab: "data", subtab: "backup", root: "#backupWorkspace" };
      }
      return { next: "manage", tab: "data", subtab: "storage", root: "#storageWorkspace" };
    }
    if (section === "performance") {
      return { next: "manage", tab: "data", subtab: "tuning", root: "#tuningWorkspace", selector: "[data-tuning-lite-card]" };
    }
    if (section === "multi_host" || section === "staging" || section === "target") {
      return { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#binding-editor-heading" } };
    }
    if (section === "weather") {
      return { next: "manage", tab: "perception", perceptionTab: "config-environment:perception-detail-place", root: "#perceptionWeatherSourceWorkspace" };
    }
    if (section === "service_balance") {
      return { next: "manage", tab: "perception", perceptionTab: "config-balance:perception-detail-balance-source", root: "#perceptionBalanceSourceWorkspace" };
    }
    if (section === "news") {
      return { next: "manage", tab: "perception", perceptionTab: "config-news:perception-detail-external", root: "#nativeModelWorkspace" };
    }
    if (section === "solar_terms") {
      return { next: "manage", tab: "perception", perceptionTab: "config-environment:perception-detail-calendar", root: "#nativeCalendarWorkspace" };
    }
    if (section === "legacy") {
      return { next: "manage", tab: "data", subtab: "migration", root: "#legacyMigrationWorkspace" };
    }
    return { next: "manage", tab: "host" };
  }

  /* Built-in canonical entries covering all 7 previously unindexed UI surfaces:
   *   1. setup-guide (陪伴通用模型、引导步骤、快速配置)
   *   2. workspaces/host (陪伴通用模型 / 共享启动模型、备用模型、账号映射、群目标、窗口合并)
   *   3. models-page (all 25 functional tasks + voice TTS + image gen + web search + Laya + pricing)
   *   4. 8 standalone pages (appearance, storage, backup, huawei-health, weather-source, semantic, self-review, behavior)
   *   (5. _conf_schema.json & 6. 4 native extensions come dynamically from /search/catalog;
   *    7. 学习与反思 8 sub-tabs are in searchIndex.) */
  const BUILTIN_MODEL_TASKS = [
    ["conversation.reply", "对话回复", "对话", "AstrBot 核心回复与 V2 附加提示", ["核心回复", "主链回复", "附加提示"]],
    ["conversation.poke_reply", "戳一戳回复", "对话", "收到戳一戳时生成简短自然的回应", ["拍一拍回复", "戳一戳模型"]],
    ["conversation.context_image_caption", "图片摘要", "对话", "为已授权的历史图片提取可见内容", ["历史图片摘要", "看图摘要"]],
    ["conversation.image_narration", "当前图片转述", "对话", "为本轮已授权图片生成简短文字描述", ["图片理解模型", "识图转述"]],
    ["conversation.video_narration", "原生视频转述", "对话", "将原视频或压缩视频交给原生视频或视觉中转模型理解", ["视频理解模型", "看视频"]],
    ["screen.interpretation", "屏幕解读", "感知", "分析已授权截图与摄像头单帧，需要支持图片的模型", ["看屏模型", "截图理解", "摄像头识别"]],
    ["conversation.image_review", "图片审核", "感知", "检查群聊成图的可见内容", ["群图审核", "生图审核"]],
    ["reality.text", "现实提醒文案", "感知", "现实提醒、唤醒回执和短回复的文字生成", ["闹钟文案", "叫醒文案"]],
    ["proactive.plan", "主动联系规划", "主动与整理", "双阶段模式下判断时机并整理新回合切口", ["主动规划模型"]],
    ["proactive.compose", "主动联系写作", "主动与整理", "按所选方式生成主动消息正文", ["主动消息写作", "主动发消息模型"]],
    ["proactive.judge", "主动联系复核", "主动与整理", "复核主动消息是否符合当前人格与来源", ["主动消息复核", "主动审核"]],
    ["memory.reflect", "事实整理", "主动与整理", "从已授权素材整理认识", ["记忆反思模型", "事实反思"]],
    ["calendar.plan", "日程规划", "生活与成长", "基于已有目标规划下一步", ["日程模型", "作息规划"]],
    ["role.state.maintain", "角色状态", "生活与成长", "维护角色的连续生活状态", ["状态维护模型", "心情能量"]],
    ["role.reading.consume", "阅读理解", "生活与成长", "阅读实际提供的原文", ["读书模型", "书架阅读"]],
    ["role.growth.progress", "成长进展", "生活与成长", "根据已完成确认提出进展", ["技能成长模型"]],
    ["role.journal.diary", "日记", "私人书写", "保存私人书写", ["写日记模型"]],
    ["role.journal.dream", "梦境", "私人书写", "保存梦境记录", ["写梦境模型"]],
    ["role.journal.derive", "书写整理", "私人书写", "整理已有文字素材", ["素材整理"]],
    ["role.journal.select", "素材选择", "私人书写", "选择本次书写素材", ["日记选材"]],
    ["role.expression.learn", "表达学习", "学习", "从当前会话的原始素材提炼表达规则", ["语气学习模型", "表达提炼"]],
    ["role.expression.review", "回复表达复核", "学习", "发送前检查规则应用是否自然", ["表达复核"]],
    ["role.expression.contact_review", "主动表达复核", "学习", "主动消息发送前检查表达", ["主动表达检查"]],
    ["role.self_review", "每日复盘", "学习", "从近期执行记录提炼调整", ["自我复盘模型", "每日反思"]],
    ["content.authoring.draft", "内容起草", "内容工作", "根据用户要求与已有素材创作或续写作品", ["创作起草", "写小说模型"]],
  ];

  function builtinSurfaceEntries() {
    const entries = [
      /* 1. 账号与会话 (workspaces/host) — unified primary entry for 陪伴通用模型 */
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "shared_model",
        sectionTitle: "陪伴通用模型（全局默认）",
        field: "model.provider_id",
        label: "陪伴通用模型（全局默认）",
        help: "原称「共享启动模型」· 插件后台任务（主动联系、日程、状态、记忆整理等）默认使用的主模型提供方。",
        aliases: ["陪伴通用模型", "共享启动模型", "插件启动模型", "全局默认模型", "全局默认", "插件任务模型 Provider", "初始主 Provider", "provider_id"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#shared-model-provider" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "shared_model",
        sectionTitle: "陪伴通用模型（全局默认）",
        field: "model.fallback_provider_id",
        label: "全局备用模型（回退 Provider）",
        help: "当主模型不可用或触发拒答替代时的全局默认备用模型提供方（原称「回复任务的回退 Provider」）。",
        aliases: ["备用模型", "回退模型", "全局备用模型", "回复任务的回退 Provider", "fallback_provider_id", "fallback"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#shared-model-fallback" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "runtime",
        sectionTitle: "运行状态与账号",
        field: "account_runtime",
        label: "Bot 账号运行状态与准备启动",
        help: "查看已配置 Bot 账号接入状态，准备并启动 V2 宿主运行。",
        aliases: ["准备并启动", "账号运行状态", "Bot 状态"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#account-runtime-heading" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "bindings",
        sectionTitle: "角色与人格映射",
        field: "role_persona_bindings",
        label: "账号下的角色与人格映射",
        help: "将 Bot 账号与 AstrBot 原生人格归入同一伴侣角色，实现私聊与群聊角色联动。",
        aliases: ["角色映射", "人格映射", "绑定角色", "多 Bot 角色"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#binding-editor-heading" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "bindings",
        sectionTitle: "角色与人格映射",
        field: "hide_unlinked_personas",
        label: "隐藏未关联窗口的人格",
        help: "在账号与角色映射列表中隐藏尚未关联会话窗口的原生人格。",
        aliases: ["隐藏未关联人格", "未关联窗口"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#hide-unlinked-personas" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "topology",
        sectionTitle: "会话拓扑与群目标",
        field: "session_topology",
        label: "账号 → 角色 → 会话窗口拓扑与窗口合并",
        help: "查看私聊与群聊窗口连接拓扑，支持私聊窗口合并与共享角色。",
        aliases: ["窗口合并", "会话窗口", "私聊合并", "拓扑"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#topology-heading" } },
      },
      {
        group: "host.workspace",
        groupTitle: "账号与会话",
        section: "groups",
        sectionTitle: "群聊目标",
        field: "group_targets",
        label: "群聊目标启用管理",
        help: "选择接入 V2 陪伴的 QQ 群聊目标并保存。",
        aliases: ["群目标", "启用群聊", "群白名单"],
        location: { next: "manage", tab: "host", workspaceFocus: { workspace: "host", selector: "#group-target-panel" } },
      },

      /* 2. 开始使用 (setup-guide.js) */
      {
        group: "setup.guide",
        groupTitle: "开始使用",
        section: "quick_model",
        sectionTitle: "快速配置 · 模型",
        field: "setup_default_model",
        label: "快速配置：陪伴通用模型与常用后台路由",
        help: "在开始使用页原地修改陪伴通用模型（全局默认主/备模型）与主动联系、日程、状态、复盘模型路由。",
        aliases: ["陪伴通用模型", "初始主 Provider", "快速配置模型", "首次模型配置"],
        location: { next: "manage", tab: "setup", setupQuickBlock: "runtime", root: "#setupGuideHost", selector: '[name="setup-provider"]' },
      },
      {
        group: "setup.guide",
        groupTitle: "开始使用",
        section: "wizard",
        sectionTitle: "首次连接与引导",
        field: "setup_core_binding",
        label: "核心管理员窗口绑定与首次引导",
        help: "绑定核心管理员私聊窗口、完成引导步骤并执行主动消息端到端测试。",
        aliases: ["核心窗口绑定", "companion_bind", "主动消息测试", "新手引导"],
        location: { next: "manage", tab: "setup", root: "#setupGuideHost" },
      },

      /* 3. 模型与连接 (models-page.js) — 6 tabs + 25 functional model tasks */
      ...BUILTIN_MODEL_TASKS.map(([kind, label, grp, desc, aliases]) => ({
        group: "models.task",
        groupTitle: "模型与连接",
        section: "fn",
        sectionTitle: `功能模型 · ${grp}`,
        field: kind,
        label: `${label}（模型路由）`,
        help: desc,
        aliases: [label, kind, `${label}模型`, `${label}备用模型`, ...aliases],
        location: { next: "manage", tab: "models", modelsTab: "fn", modelsTask: kind, root: "#modelWorkspace" },
      })),
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "voice",
        sectionTitle: "语音模型（TTS）",
        field: "tts_providers",
        label: "TTS 语音服务管理、发音人音色与连接试听",
        help: "配置在线/本地 TTS 语音提供方（Fish Audio / IndexTTS2 / GPT-SoVITS 等）、音色与测试合成。",
        aliases: ["TTS", "语音服务", "发音人", "音色", "Fish Audio", "IndexTTS2", "试听"],
        location: { next: "manage", tab: "models", modelsTab: "voice", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "image",
        sectionTitle: "生图模型",
        field: "image_generation_providers",
        label: "生图模型连接（在线模型 / ComfyUI / SD WebUI）",
        help: "配置文生图与参考图生图服务、工作流模板、优先级与开关。",
        aliases: ["生图模型", "ComfyUI", "SD WebUI", "在线生图", "文生图", "改图", "画图模型"],
        location: { next: "manage", tab: "models", modelsTab: "image", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "search",
        sectionTitle: "联网搜索",
        field: "web_search_provider",
        label: "联网搜索模型（DeepSeek / 外部搜索服务）",
        help: "配置角色自主联网检索使用的搜索 Provider 与连接测试。",
        aliases: ["联网搜索", "DeepSeek 搜索", "搜索服务", "网页搜索模型"],
        location: { next: "manage", tab: "models", modelsTab: "search", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "laya",
        sectionTitle: "Laya 决策模型",
        field: "laya_decision_model",
        label: "Laya 决策接口、本地检查点与运行日志",
        help: "配置本地或远程 Laya 决策分类模型接入方式、地址、超时与分类开关。",
        aliases: ["Laya", "外置决策", "决策分类模型", "SystemOne"],
        location: { next: "manage", tab: "models", modelsTab: "laya", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "pricing",
        sectionTitle: "计价与汇率",
        field: "model_pricing_fx",
        label: "模型单价、缓存费率、币种显示与实时汇率",
        help: "配置各模型输入/缓存/输出百万 Token 单价、展示币种（CNY/USD）与汇率换算。",
        aliases: ["模型单价", "汇率", "计价", "费用统计", "费率", "Token 价格"],
        location: { next: "manage", tab: "models", modelsTab: "pricing", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "quota",
        sectionTitle: "额度看板",
        field: "model_quota_dashboard",
        label: "多站点额度看板、自动识别接入与余额感知联动",
        help: "统一监控多个中转站与官方 API 剩余余额，支持自动识别站点协议、汇率汇总、双单位切换与订阅余量查看。",
        aliases: ["额度看板", "多站点余额", "中转站余额", "NewAPI 额度", "神人站", "quota", "余额监控", "余额看板", "账户余额", "双单位", "订阅余量"],
        location: { next: "manage", tab: "models", modelsTab: "quota", root: "#modelWorkspace" },
      },
      {
        group: "models.tab",
        groupTitle: "模型与连接",
        section: "quota",
        sectionTitle: "额度看板 · 余额感知",
        field: "quota_awareness_primary_station",
        label: "额度看板 · 指定主站与陪伴余额感知策略",
        help: "指定陪伴人格主动低余额提醒与账户余额卡读取的主站点，或切换为多站折算汇总 / 任一偏低触发。",
        aliases: ["指定主站", "主站", "主站点", "切换主站", "默认站点", "余额感知主站", "多站点汇总", "余额感知策略", "账户余额主站", "primary_station_id"],
        location: {
          next: "manage",
          tab: "models",
          modelsTab: "quota",
          quotaFocus: "awareness",
          root: "#modelWorkspace",
          selector: '[data-qw-aw-field="primary_station_id"]',
        },
      },

      /* 4. 8 Independent Standalone Pages */
      /* 4a. appearance-page.js */
      {
        group: "appearance.page",
        groupTitle: "外观与设置",
        section: "theme",
        sectionTitle: "主题色彩",
        field: "appearance_theme",
        label: "小屋配色主题（庭院 / 晨光 / 暮色 / 夜间 / 胡桃 / 高对比）",
        help: "切换陪伴小屋整体配色主题，并与工作区自动同步。",
        aliases: ["主题", "深色模式", "暗色主题", "夜间模式", "鼠尾草绿", "高对比度"],
        location: { next: "manage", tab: "appearance", root: "#appearanceWorkspace", selector: "[data-ap-theme]" },
      },
      {
        group: "appearance.page",
        groupTitle: "外观与设置",
        section: "theme",
        sectionTitle: "自动光照",
        field: "appearance_follow_light",
        label: "跟随小屋光照自动切换深浅主题",
        help: "开启后，小屋进入夜晚时自动切换至深色主题。",
        aliases: ["跟随光照", "自动深色", "昼夜主题"],
        location: { next: "manage", tab: "appearance", root: "#appearanceWorkspace", selector: "[data-ap-follow]" },
      },
      {
        group: "appearance.page",
        groupTitle: "外观与设置",
        section: "layout",
        sectionTitle: "排版与动效",
        field: "appearance_density",
        label: "界面密度、圆角、字号与动效偏好",
        help: "调整紧凑/标准/宽松密度、卡片圆角、字号缩放、大字模式与减少动态效果。",
        aliases: ["密度", "圆角", "字号", "大字模式", "减少动效", "动效"],
        location: { next: "manage", tab: "appearance", root: "#appearanceWorkspace", selector: "[data-ap-dim]" },
      },
      /* 4b. storage-page.js & tuning-page.js */
      {
        group: "storage.page",
        groupTitle: "资料与维护",
        section: "storage",
        sectionTitle: "插件存储管理",
        field: "storage_directory_and_cleanup",
        label: "插件数据目录、自动清理与缓存清理",
        help: "查看 SQLite 数据库与媒体附件空间占用、配置自动清理保留策略并一键清理缓存。",
        aliases: ["存储空间", "数据目录", "清理缓存", "自动清理", "磁盘占用"],
        location: { next: "manage", tab: "data", subtab: "storage", root: "#storageWorkspace", selector: "[data-storage-form], [data-storage-cache-clear]" },
      },
      {
        group: "storage.page",
        groupTitle: "资料与维护",
        section: "storage",
        sectionTitle: "数据库体检与瘦身",
        field: "storage_database_health_compaction",
        label: "数据库体检与一键瘦身",
        help: "扫描主库与副库体积、重复上下文、冷遥测明细和空闲页碎片，支持在线常规无损瘦身与全量深度重组（VACUUM）。",
        aliases: ["数据库瘦身", "数据库体检", "一键瘦身", "瘦身", "体检", "压缩", "释放空间", "清理空闲页", "VACUUM", "SQLite 瘦身", "重复上下文去重", "冷遥测归档"],
        location: { next: "manage", tab: "data", subtab: "storage", root: "#storageWorkspace", selector: "[data-db-health-card]" },
      },
      {
        group: "storage.page",
        groupTitle: "资料与维护",
        section: "storage",
        sectionTitle: "插件存储管理",
        field: "storage_auto_cleanup",
        label: "开启定期自动清理旧记录与临时文件",
        help: "按保留天数与容量上限定期清理旧日志和临时附件。",
        aliases: ["自动清理开关", "定时清理"],
        location: { next: "manage", tab: "data", subtab: "storage", root: "#storageWorkspace", selector: "[data-storage-auto-toggle]" },
      },
      {
        group: "tuning.page",
        groupTitle: "资料与维护",
        section: "tuning",
        sectionTitle: "性能调优",
        field: "performance_tuning_profile",
        label: "性能调优档位（自动 / 省内存 / 平衡 / 高性能 / 兼容稳妥）",
        help: "自动识别本机内存（含容器限额）与 CPU 核心数并推荐档位，支持五档一键切换、机械硬盘兼容稳妥模式与防爆内存保护。",
        aliases: ["性能调优", "性能档位", "自动推荐", "省内存轻量化", "省内存", "低配机", "平衡推荐", "高性能流畅", "高性能", "兼容稳妥", "机械硬盘", "HDD", "NAS", "卡顿", "防卡顿", "auto", "eco", "balanced", "turbo", "compat"],
        location: { next: "manage", tab: "data", subtab: "tuning", root: "#tuningWorkspace", selector: "[data-tuning-profiles]" },
      },
      ...[
        ["cache_size_mb", "主库内存缓存", "数据库读写", "调大翻聊天记录和状态更快但多占内存；调小更省内存。", ["主库缓存", "SQLite缓存", "cache_size", "内存缓存"]],
        ["sidecar_cache_size_mb", "副库内存缓存", "数据库读写", "图库、语义索引与归档等附属库的内存缓存大小。", ["副库缓存", "附属库缓存", "sidecar_cache"]],
        ["mmap_size_mb", "数据库内存映射", "数据库读写", "固态硬盘大库调大可显著提速；设为 0 最省内存且适合机械硬盘与网络盘。", ["内存映射", "mmap", "mmap_size"]],
        ["sidecar_mmap_size_mb", "副库内存映射", "数据库读写", "附属库内存映射上限；设为 0 兼容老旧磁盘与小内存设备。", ["副库映射", "sidecar_mmap"]],
        ["reader_pool_size", "同时查库通道数", "数据库读写", "允许多个页面或群聊并发查库；机械硬盘建议调小防磁头争抢。", ["连库数", "读连接池", "只读连接", "reader_pool_size"]],
        ["journal_size_limit_mb", "写入日志截断阈值", "数据库读写", "WAL 临时写入日志超过阈值后自动截断合并回主库。", ["WAL上限", "日志截断", "journal_size_limit"]],
        ["synchronous", "写盘安全策略", "数据库读写", "常规（NORMAL）兼顾速度与日常安全；最稳（FULL）每笔落盘防断电丢数。", ["写盘策略", "同步模式", "synchronous", "断电保护"]],
        ["write_batch_window_ms", "写入合并等待窗口", "任务与并发", "将毫秒内零碎统计合并成一次写盘，调大更护硬盘，设为 0 即时落盘。", ["批量写入", "合并写入", "write_batch_window_ms"]],
        ["background_slots", "后台并发任务数", "任务与并发", "同时运行日记、反思与主动联系等后台任务的并发槽位数。", ["后台任务并发", "后台槽位", "background_slots"]],
        ["tts_max_concurrency", "语音同时合成数", "任务与并发", "多段语音回复同时合成并发数，调小可避免触发语音接口限流。", ["TTS并发", "语音并发", "tts_max_concurrency"]],
        ["telemetry_ttl_days", "运行明细保留天数", "归档与清理", "超过天数的调用明细在瘦身时归档至冷库，每日统计完整保留。", ["遥测保留", "明细保留", "冷归档天数", "telemetry_ttl_days"]],
      ].map(([field, label, sectionTitle, help, aliases]) => ({
        group: "tuning.page",
        groupTitle: "性能调优",
        section: "tuning",
        sectionTitle,
        field,
        label,
        help,
        aliases,
        location: { next: "manage", tab: "data", subtab: "tuning", root: "#tuningWorkspace", tuningField: field, selector: `[data-tuning-field="${field}"]` },
      })),
      /* 4c. backup-page.js */
      {
        group: "backup.page",
        groupTitle: "资料与维护",
        section: "backup",
        sectionTitle: "备份与还原",
        field: "backup_create_and_restore",
        label: "创建数据备份、预览校验与一键还原快照",
        help: "创建插件完整数据快照，支持还原前试算校验与安全隔离旧数据。",
        aliases: ["创建备份", "还原备份", "数据快照", "恢复数据"],
        location: { next: "manage", tab: "data", subtab: "backup", root: "#backupWorkspace", selector: "[data-backup-create]" },
      },
      /* 4d. huawei-health-page.js */
      {
        group: "huawei.health",
        groupTitle: "感知",
        section: "health",
        sectionTitle: "华为运动健康",
        field: "huawei_health_sync",
        label: "华为运动健康账号登录与健康数据同步",
        help: "配置华为运动健康账号/Cookie、自动信任登录与步数/心率/睡眠数据感知。",
        aliases: ["华为健康", "运动健康", "心率", "步数", "睡眠数据", "健康感知"],
        location: { next: "manage", tab: "perception", perceptionTab: "config-screen:perception-detail-health", root: "#huaweiHealthWorkspace", selector: "[data-hw-account], [data-hw-action]" },
      },
      /* 4e. weather-source-page.js */
      {
        group: "weather.source",
        groupTitle: "感知",
        section: "weather",
        sectionTitle: "天气数据源",
        field: "weather_source_provider",
        label: "实况天气数据源（和风天气 / Open-Meteo）与鉴权",
        help: "选择实况天气提供方，配置和风专属 API Host、API Key 或 Ed25519 JWT 私钥与缓存间隔。",
        aliases: ["天气数据源", "和风天气", "QWeather", "Open-Meteo", "实况天气", "天气密钥"],
        location: { next: "manage", tab: "perception", perceptionTab: "config-environment:perception-detail-place", root: "#perceptionWeatherSourceWorkspace", selector: "[data-weather-source-form]" },
      },
      /* 4f. semantic-page.js */
      {
        group: "semantic.page",
        groupTitle: "学习与反思",
        section: "semantic",
        sectionTitle: "语义目录",
        field: "semantic_catalog_policy",
        label: "全局语义目录、语义模型与素材归类",
        help: "管理全局语义索引、语义向量策略与观察素材分类。",
        aliases: ["语义目录", "全局语义策略", "语义素材", "语义索引"],
        location: { next: "manage", tab: "learning", subtab: "semantic", root: "#semanticLearningWorkspace" },
      },
      /* 4g. self-review-page.js */
      {
        group: "self.review",
        groupTitle: "学习与反思",
        section: "reflection",
        sectionTitle: "每日复盘",
        field: "self_review_enabled",
        label: "启用每日自我复盘（角色自我反思开关）",
        help: "开启后角色每天从近期执行与互动记录中提炼待确认的改进建议。",
        aliases: ["每日复盘", "自我复盘开关", "每日反思", "self_review"],
        location: { next: "manage", tab: "learning", subtab: "reflection", root: "#selfReviewWorkspace", selector: "[data-review-enabled]" },
      },
      /* 4h. behavior-page.js */
      {
        group: "behavior.page",
        groupTitle: "学习与反思",
        section: "behavior",
        sectionTitle: "行为模仿",
        field: "behavior_learned_habits",
        label: "已学习的群聊行为习惯管理与启用开关",
        help: "查看、筛选并逐条启用或停用角色从群聊中习得的互动习惯。",
        aliases: ["行为模仿", "群聊习惯", "学到的群聊互动", "行为学习"],
        location: { next: "manage", tab: "learning", subtab: "behavior", root: "#behaviorLearningWorkspace", selector: "[data-behavior-filter], [data-behavior-toggle]" },
      },
    ];
    return entries;
  }

  function catalogEntries(catalog) {
    if (!catalog || typeof catalog !== "object") return [];
    const entries = [];

    /* 1. Backend-registered model tasks (merges with & extends static builtin tasks) */
    if (Array.isArray(catalog.model_tasks)) {
      for (const task of catalog.model_tasks) {
        if (!task?.kind) continue;
        const label = task.label || task.kind;
        entries.push({
          group: "models.task",
          groupTitle: "模型与连接",
          section: "fn",
          sectionTitle: task.group ? `功能模型 · ${task.group}` : "功能模型",
          field: task.kind,
          label: `${label}（模型路由）`,
          help: task.description || "V2 模型任务路由配置",
          aliases: [label, task.kind, `${label}模型`, `${label}备用模型`, ...(Array.isArray(task.aliases) ? task.aliases : [])],
          location: { next: "manage", tab: "models", modelsTab: "fn", modelsTask: task.kind, root: "#modelWorkspace" },
        });
      }
    }

    /* 2. _conf_schema.json visible global plugin settings (all 107 visible keys;
     *    model.provider_id & model.fallback_provider_id are already registered
     *    under host.workspace with identical field keys to avoid duplicate top rows). */
    if (Array.isArray(catalog.plugin_config)) {
      for (const item of catalog.plugin_config) {
        if (!item?.path) continue;
        if (item.path === "model.provider_id" || item.path === "model.fallback_provider_id") continue;
        const aliases = [item.path, item.field, item.description].filter(Boolean);
        entries.push({
          group: `plugin_config.${item.section || "global"}`,
          groupTitle: "插件全局配置",
          section: item.section || "global",
          sectionTitle: item.section_title || item.section || "全局设置",
          field: item.path,
          label: item.label || item.description || item.path,
          help: item.hint || item.section_hint || "插件全局配置项（AstrBot 插件配置 / 陪伴小屋联动）",
          aliases,
          location: pluginConfigLocation(item),
        });
      }
    }

    /* 3. Native extension schemas (game / live / reality / together — all 309 fields) */
    if (Array.isArray(catalog.native_extensions)) {
      for (const ext of catalog.native_extensions) {
        if (!ext?.id || !Array.isArray(ext.items)) continue;
        const extName = ext.name || ext.id;
        for (const item of ext.items) {
          if (!item?.path) continue;
          const isRealityMobile = ext.id === "reality" && String(item.path).startsWith("mobile.");
          const location = isRealityMobile
            ? {
                next: "manage",
                tab: "mobile",
                nativeExtension: { id: "reality", field: item.path, inline: true },
              }
            : {
                next: "manage",
                tab: "all",
                nativeExtension: { id: ext.id, field: item.path, inline: false },
              };
          entries.push({
            group: `native_ext.${ext.id}`,
            groupTitle: `原生扩展 · ${extName}`,
            section: item.group || "general",
            sectionTitle: item.group_title || extName,
            field: item.path,
            label: item.label || item.path,
            help: item.hint || `${extName}扩展设置项`,
            aliases: [extName, ext.id, `${ext.id}.${item.path}`, item.group_title].filter(Boolean),
            location,
          });
        }
      }
    }

    return entries;
  }

  function setCatalog(catalog) {
    backendCatalog = catalog && typeof catalog === "object" ? catalog : null;
    notifyRegistryChange();
  }

  function collectSettings() {
    const out = [];
    for (const [, fn] of registrySources) {
      try {
        const items = fn();
        if (Array.isArray(items)) out.push(...items);
      } catch (_) {
        /* isolate individual source failures */
      }
    }
    if (backendCatalog) {
      out.push(...catalogEntries(backendCatalog));
    }
    return out;
  }

  /* Register built-in surfaces immediately so offline/pre-fetch search still covers all 7 surfaces. */
  registerSource("builtin.surfaces", builtinSurfaceEntries);

  const CompanionSearchEngine = {
    normalize,
    tokenize,
    cjkNgrams,
    toPinyin,
    matchPinyinMeta,
    SYNONYM_GROUPS,
    expandAliases,
    editDistanceAtMostOne,
    fuzzyWindowMatch,
    orderedSubsequenceScore,
    prepare,
    parseQuery,
    scoreEntry,
    search,
    highlight,
    escapeHTML,
    escapePattern,
  };

  const CompanionSearchRegistry = {
    register: registerSource,
    unregister: unregisterSource,
    listSources: () => [...registrySources.keys()],
    onChange: onRegistryChange,
    setCatalog,
    getCatalog: () => backendCatalog,
    collectSettings,
    pluginConfigLocation,
    BUILTIN_MODEL_TASKS,
  };

  window.CompanionSearchEngine = CompanionSearchEngine;
  window.CompanionSearchRegistry = CompanionSearchRegistry;
})();
