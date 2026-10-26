"""Closed Task 7 usage-route registry and typed usage-evidence extraction."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import unicodedata
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, NoReturn, cast
from uuid import UUID

from idis.pipeline.contract_registry import FULL_STRICT_V21_CONTRACT_HASH
from idis.pipeline.task7_census import CENSUS_SCHEMA_REGISTRY

CONSUMER_RECEIPT_INCOMPLETE: Final = "CONSUMER_RECEIPT_INCOMPLETE"
PIPELINE_CONTRACT_HASH_MISMATCH: Final = "PIPELINE_CONTRACT_HASH_MISMATCH"

_FULL_STRICT_V21_VERSION: Final = "full-strict-v21"
_USAGE_ROUTE_REGISTRY_SHA256: Final = (
    "96cbe37825fca73179b8077d7a81fb235aaf057aff405342bac18b7cf365c616"
)
_USAGE_EXTRACTOR_REGISTRY_SHA256: Final = (
    "e6542bb692bb4819712bc95bfdd23c2f66f7fd21fa15e0b7881057ff81eeeccc"
)
_HASH_RE: Final = re.compile(r"^[0-9a-f]{64}$")

# Generated once from the append-frozen v18 artifact. The compressed literal is a
# code-owned copy of all 1,756 route tuples plus the 19 extractor definitions;
# runtime never reads a contract file from disk.
_USAGE_AUTHORITY_B85: Final = (
    "c-rl~TXW;awx#=D{Jecm-Y<KHA7qKP6-y;mODeDJUa^A#0-(ewiPR#gDpz-$|9%prE(8fmOn^e>oM1;sw`&Q"
    "!j1NiV%rOU$|M#cq<Z+(Qe)`XU{fqp^Pd!3t|LWq)<#Dfndj4nc{QBbZul~`=r_-ye(~I+;{^O^kvwt4_b>;"
    "r!<mmdSe|d57Y30DrN5}s-`n~7=+V9Wi`9r_&{&W59ivQ{Q==#f5|Kj}Yua#H-N!_1D<8S$RKAHabInBqj$J"
    "x){ivRWh9gQCz=ly9uoVkB3-g=s+`RHNZfB8#3&Btk8{C)Vf|BY=P&`&4s9q;pL)tCC8|M(YO=x5LR|2R57x"
    "%ln3_tpR2ySjFNettov`q#g$YJdKSh4R~EoK1$qR&+mq9jBTtRP&Yd&^x+x-|gzU_xY-x<ld*F^Xt=NciiVM"
    "*Wo3XzmAj4ca^*p+CToIcl-|zjjJzv9@RJbZ2oh4o2OsfRx5Ts!57D0KDk5spD%mAot^&v$F+y%m)9%>Ke(@"
    "TH@dl<x2;w#)QT0gC%v=NKYN!)zn=B_m%ZbQ^W)RA)1&M4AN1={#XsGrPR8lzZj{VNld=0B%CCGuu>H-8fA&"
    "s4Kk>-&yV19NnhfsppUdz4v4ASSw!@?rnB>dP@$>0tr{uf`ee`c!-t-PaArAZvp=Jl6pL*AST%25-UHtAY0{"
    "?w|d33xO71u{s|LA`{J32pi=k6Wpt8el0y7E|`C$q0VKinnbwh`Ed2zvkBJO1)?Vc3<y%WKLE?(_NWB%9n#Z"
    "hrin|DC6gAOBO|<Uz>}K%O8J*SzGWc)xl6_q<B=(}DfVqu=}PqH^v2UDoVgH%-#{&+dJGetMrb6yN0Id^$?|"
    "Pp@)#w(EXAc30hW2i?D49)13!-~03Qq<4PYTU@2)`E;Dz_499|toZyY2+LQUAARawxnj3h25>V?9&UdgC-?d"
    "6AxZPM#1}`pf6N~5-Z<UMqjnX|b`V{hJ73AgYqkh3&h##Fi;*=NXZhc64DHQB-qE|P;?#@YwHbWd%>FEO`Dt"
    "rYc-6Fiba{RH+hX8vo6wW#d^9XZ{&oT@hxeZk-wtE-=v{G$dN>}Y6Sv?>3LweDLq5(%h?p#kLH%5kTwj!Ccw"
    "B`qU6yoa<qM%N0bb8n0YL8r0Q`Jc7HxJC4STXHE4Di+20PQ0#Ij`tXWalSF1^3^7T2)XpJ}m%m;Iy9pU+N@i"
    "wiThANZ@X7Xk|`ivo|55|)#rt3Q6dIJ!L9cDcL0{Br$A>9AcPyu4;@zDs7egGn;Y+P>DaMXg8atNtBl`}y>7"
    "e%pWf-au#i(oo<vQT#jU52mAhh=GBq$snEFyLU@R?z)(bT-EY)VFb@Z*5ol96_qeL9I7Uc>1g)Vz46V+UBsp"
    "sK<JtvP^=kCBuw2Q@i^|gH@r`#KQLghH8J2>HLv2}q&|-9(S41<<2Z94bf1hz!+bW!fMi)|vSl6Ys?1f7*t7"
    "ZuZZB`PQI}OHcVF%5%h|$SIX*i&{j{zAUi`8D>FBz5d3tnq`oE7nSu9^);*j1Yqx+xl6E{D(S$p(<TIi@Pzm"
    "fZbPR9As*#g9}1{F`=uoxvzE6@Jb(fQHV1<A`%r`NqEU|!y*Y)?E*TH*1CdUzIp>>pqDmc2{2!vBX`yY_#-J"
    "Ua1P!!F;b1ZS3vlPo-$WuYoxfjBr_XR@qT)zde0K%NddJ~}(zhUKK^-r&>e`RP@mw>a|4nfK+zy=v|1lH8?_"
    "cY!c0YgN6f9<|F+#b8;bn*Q3~`X?9vJioejKY~wqOwKC()=NKF%d<kdAYRfdXG8Z(C~_zlUoPM4=TyC9?NTs"
    "#oW50pzN`@E)5qDozc_q5kF`I)$AZ>o@uw>%PF!7a@=B#Tqq$Dx&)(-UfN%N38}h%o`66=@NA@z<`fk*GO>X"
    "k-5ZpWNj(~X~IO+ZBM7#e;vTu{reXlBs%Y*ut7iYco)yKfS<mc(+E`M{;Q5@ATj(TgYu{daFnY^CN#mwCwO!"
    "MSxRjnSUR#8uD(P^6|*=X598y1%pFp_0G$qOHDG8~RlI8s&3AMA86n!{14y;Qn__Ux~#)3Vd-u94%){`2$Ee"
    "Rs7!Uz?8H@BQw_Y&3gM^z(7cPuZ^f<+z=do4U&Hlj+xd4pp|TD|?=&x8qR?_Jm7PU-|TY230k!uR6JhDw@?5"
    "y*g1r70>I6za7oylj$fKLsc&7Dxc-4Q{JCF<i(8UFhC{XSD#%SUtGR#x;-7WX0@emq>OLga#WT_Y*)8&z<c6"
    "U?N#L2(O<nw<`fhrWAEx}$2H`}%gXjb?uz(vKAXJ(;V$`+PYd7Z>Cm^VrHw=0<Kb7kJ&7sY#XpYD&U)ty`J="
    "S|))MoXz(&cs6!zXgf0qAd$@+BA&a%yZWT&^u-CaJusjk&y6mIu)zPGPXB2=xGWt-hp-KD@-6&TtK4Dk3^6%"
    "p2q2%<1q6&>D;4xVUP6&%qF4u+sv6&BeH3yRoT6&KZTxPT9#RYB3spx}w5RlzZh;UEgARRLO5w~X{x?^;^K)"
    "t>Kms<;7aW${)VzH#GI)y+;V+~Blw!`0#r{zVDP=!-NbtH`^o-{5re-g<O#%tn7d%Re^P@O$sPce(f$uV+gy"
    "iciLtIu^mXwQ$SRzu%G*Ft1XnX;D5h-e3PC%Ctr$0q#ctY+j>Myzmo`C$UjUL)NDOGP6+$LHR)dk=m#fs%r|"
    "3KDSY+*3?uBCAm>4+SU{udv>ErtRZ!OxbdbpDs@snolx@|mD*Wt?QjwtmBM*#;h-`cmD)uE?Lbl-mAYjE-7s"
    "<-m9kZB+0c?4mGX6M`Jl2KmD){h?I6<}l?*&<wuti_Rq`yUzs7{0=%^&(S<ivYbW{pwdlDXZs-sdm-`6>hH`"
    "h_Ads)hf3f;T4L}uwU&&S0TXEshAX19}h{~?1KXQ(73_fIIBq<>>Vq#7b}znlB~eu8b}=sjV<He5_YF2F-ml"
    "}v0yCQpy=_?82YG*rQ%gfygsC{$Jrh&BX-Cs$U@hc)DbAzD^VhBqYhTyd^c{iygfEs#;9@2b%>t&HIZ@T$2q"
    "Er;=B@T&Rf=6n#v@TwUtYh>9kq8rzz*MBXaI)70_IWC{ah`X>%`=jiaS)TUu>?WE1SMex0s$LE(KUA)F?mp&"
    "^;tBOjdv^Jn#j~QH4u6Toyd3%ZRu^Bck1sy0ITz30;^)cZ+|^k5M?a^%^gS1xtOqCe4IkWC$!GoB$>S{dptb"
    "f&*vyvux$Ma7W;DNj9C)Bwz5qBO>Op8Y%`*=^#o@r|FdsUj@z`$wJ-q@rBksfK{=tXO{R40|!h_BC$@DJk50"
    "dd$4@9po0!~VLkjiK2_oSaD^W<)F;{mJmI^evNH?Ps`!K-dsc@=P0+Jn`<-RPM1CvGj$&)k1{@LGE%aArPL`"
    "E8PZ&8Or1`^*E^>x+Ps@~O*j+!|@_Q(_j!!{)<$YVe0-o{#6VZ=?LX2c?x)0cYh?jek#m4BT(Xr&w6N0yra|"
    "n*3%mxw*^x#Z{(1o%vjz)?NvmnNN+LKTMXh*z#xPQ!$iZ37nbm@}=&R@qCo_XX)+a&eNVMzZ5t%pE~{_pFZ5"
    ">e~;!rd@lAcF9J?#S;4DUQWw{MxFe1)&ab|FTEg143FXp5E^O4Zv(w)fcf7fm{pN^0ZzEo8Kv&KtzI@Ef?Xc"
    "xntlbMZ9^Q@K^oE}gdFvLy^&?i1Jb#}h4DA79ryF56<i>I%Y+1MQ(7nU$uN$hPxc#*(*?LHR^9G^~vGJs<ON"
    "y7f1o<moyKM-JTv|`==FLK26tlXDS8ovlqn6iIyK-X?7_p+B*vi|1z=)RhL~q^%1V*u{r+E9VKVXzED=+=3y"
    "o+)8G7_^tB!C0WWhS;J6ZnDm5{KnBI}JEs84$}oc4~mY65lS<U^QtViffmF@R~sI#IwshL`@zT;@D*pvMvem"
    "8QL-jRg(jr%xsy5uFnHeMz+jkS&5oWFWdb;pB9^SR;Obhrg=6hZpi*+yK9>N{p24OkC}4+D7)ve*{16RmVE5"
    "~_^i3ByU~flUads4+<s_3X!g*3s9+bk|4>bh?vGmK(=jSI1bIG2wR-k3V`jI9XH=kx=<ylV!1(@v?ec7mHxP"
    "w-)Iv2E(E^v!qc^~ik!_5amnU<8!J_tu<;`V71(4V)!fIgp!+=$<FVKLiTT(yeM1J{ndUf@7T;<KCbp^+GIs"
    "-3y3Xw}(woP1X9dBT)6ls%HWrq<OG5X=ecJo^yU)^45vbJfio0B*&^0qN<JU9UdN6|LN>fQkkn11Vq_4~g&;"
    "s+|VZ%W8qL8bQ%s5{=EGW%u+&mmOyBgYR~pHP#1W5eVYYT6ndke;Dt?TsFA&Y|Y}hSLfEP>T=7hTBEd&C2m^"
    "dWl-K4-K}XsCE0qF!+kvw8X7L?xMC~n4<I;4G9Air_*R?7@COvM&q~UczSkXM{GvJ_XG!-yJ*CoxOB%<G;&Y"
    "K;CYEgeZXizYatrFCt8@CLt{P~3P{7y*bg5o;Cw<$_Qa$U_MoL7LkqVnXloVCwOpFbhou0sXe@7$)yjiMpkW"
    "fNVcPyI3uw&b$1>}X>1S1|C{cQSR=3I!r^RReD*eSy6&-P-tlbM5TwW9-WG_@Y<3uq+_W}%@55)-E`$)m+LN"
    "UVk!U~ZG#fbP=BoG}aM&t*N6KMVuBWf>1y5K%BqCbKaYVV1W8U=DEV`z-nmPmolo-s;V!i2?lVw8R)vF^A|j"
    "ItI%V)UFC<t-9K={PYeT11F9Nyez>Qs8M9)oz$~jH-RnLFV64y)Q7`vGAzb7c+P+9<?7aUeFqOjO+^+CNGcC"
    "52pgs&SUK3M+-PdkMVs$>4d4r#0TNR?d!36<#RW!J*L`&1>4<Yx;;_|?kHu<d@!#L*?Y{k%Mzu-$4R>oahiO"
    "bwo4JQ&&NI22=@<N@%T932N`7k9vAz7(j9M)%YE>I=j(B`?{R|G)8l#{s4)3?+<YhzkX|0QA3I9G`FMit14t"
    ")4JV8GI6>k4d_?N}q^zH<22NZ1IPKb6WVessP{6JP6^6P|Zk0VO2PU!Xk;`Hf+X^$XckIr+y)bEq&UDh8Y<F"
    "8$@>O6N%eH#!m$If$a)VJAm$GG#{RP}9a@ccW^EmC(MHE3--&&^ZcCWgt)^V~bNCK!;Wp6C9lb;k@iZ_jg^)"
    "VH~G!s7GXAJvN(Zl_PyF0Q+2_$ksVFWA1HqOHP0b5E{fAJ45rR-odo<3#BQD$zPeoW`J%t%F4D4|?vOn$Obj"
    "N#B)x$=&3pD;A;W7OL+uLgo^B?vna0p6(cho_njlOAek_=($a**VsX87kciV`Ytp~j-ltysrA`_Gz~qsPHix"
    "9!1;!r+oQgVrW4ko=ccH>&~Uqlx=HEXK|@UIwt1oP#I$JzU59KjZ9@=6>5Lf?0wqp!%+y&LjAjqXv@2$gVf!"
    "S6%*ru*pK!Wk<QTC}Zt!dzBR^`~pfz!f+9xth7LL)akO67n7}L(E0cYPByH7BkFmEjRKxDYB8(Y83?q>4KvU"
    "Y*NcDgKYmlhhI%Ze7jb;#wivVEQ?Jua)-2Z__+vbueei1S~byR7~@nJ3f!#1(Y?%>8FqOheBNS9hR<%Q)nod"
    "#~;Qs59mv_uPhc2X^2LMDDpGtLON^Y9eya%~^NAhR8_dp8K>mp##xO<epo$HX1$93`Op_d+QExx?n1D&y8ID"
    "v7t5=IcZjoch4*1#I{cii(kemX$f9;JTp#d7^WC~Gfr6;m?*t7?zu<oZIXV?r{nzltRp@T_uK*YZ9vE@9?xA"
    "}-)7StcgJ(P*SE33Gj=>Tb=`f`p!IY-w{(4*7$!T%bHCS`U_d%Ko_o929W&re9M9cd-{#T@|HgCQS1)3?ts7"
    "RmxbC29!$hmF&=@vMw#p5`o&cPwk4M%adyDDTaiVm#m}#9OPE*TsZ`m975%aG2S)N<QZe@ea%JSR;b}N?dxL"
    "BTBy>2B2&%pBB%5|r)g4Vn8+~RdBEKIhQ=QghXOh7tTo}0YZ7%AY)D$mVaw<76;PvyDK>#neHTU5MGsoX(#N"
    "|3fVp)sZeZ3`I&PfD;4rPU!jO7J#GqI9B!XcHk$6UuYr*#}pHjOVj&qx^eUtSHYdW#1u$%#iZjF7_Qb-La)S"
    "H;H|R8$5H$b9Yydaf8;P^4#zB9b}k{D$m_s>yiO!S9xyfT6@%hGp#%~dwmB?C#);a-Cq5W;Wn^j{W80j*D%l"
    "1+pz-<^|@PIs&;{)@w23E0bGZC8fn_+iPEi+wtbK|JsaQMXp<j<B>mbIzs7g_+3j?Yc{RTK%WenL9iPT`6WQ"
    "&|;CVE@8^7*0UeNk8zT3NQ$A!t8@!Y1h4i%8TjOW&@wMGj#PsVdY);FMZ!jJLXg>`paxV;!%uY9g$E8MyzoS"
    "S!(F;qK2)AnG&&R%KO9w`h_BF#UTSBFe0E!t&?(xB3^U5Gf%DbLMeZzhu)_vXd@f&J;MGtX=C+!A&NO32JA&"
    "y8Pq0Ms4R%5#g?9oWG$uRQl=^&CHFO)SrCSa-mN$;|THg0%@9kfxUBF07444>)tnbF0-I;B>;|^4y`-9~*A7"
    "%QP#;JLuyW-99li=_6y>hljxoGqxpe9Wuo%3BwenIc8}Xm^e)`-(6)NCd+5TFGF!%G0A*)lYO5OGLy`A*Vy*"
    "~b;l(0-68gUcJNFx-_2e9#t&MP%y%c(_pxCz$vih^Z9)g6N#?mFYopNv%_I|^+p+Edrwb;T@Z5>j9~)|uOps"
    ">fcqi}B6NI-94U11Eh-e93cl;hfWEiFx{T@M77?>#i9^tvU>@2@e#&cJ|&C=V+T{7*6;UhdZk=+FoF2_fBZW"
    "_A_q0U%7!gJHuUEqQ9e1zvNuf8J)tLY;=H+0>F8zR?7c<#{J2oFTtM|keg+HCxQ^L;$`Xx)WQCyXD@O<HSk!"
    "|nXghJ|@ItsiOvP($PWP}>&04w*lU1cHjv{b4i^O`P_R=iamr`Sjr~|9dq5(G}yza|_zH0U@(|Ja?RZn@xAj"
    "9?uPD-^K>d=JDJ-cK1<(*5L8nANFlxn5-So4PR@50cq-ZZsA&Y%z(3VJhyLsn@cB*9M7Fyy@=tqaCq(Fx_dX"
    "b3C>%Eg~qet;^Vn>$gSbBb(|=D8m?OBh|{52Zec4{KrX)X%M#hGaIf7zHhizNpJzA8^uHEgD4X0Tqwz0QKiv"
    "O%NT$UF=^6VUr`P|rZcIEm1Fj67e9yCA=F`U<k=;}9T%tCyqDEjGy;5GTOzaxvtKO%h^Xt>&ebPm5E4@H{+F"
    "rLMs4z^YS2#4(h(l#bz4Byj@`P?!y<+20U;|@ky@C^Qj5iQw*DE#ICbgBN^>9=;IwNo)l2>vn5IK+z&MP(@2"
    "pizX?7YHTtUWkcgID2~?hD{<rq1eu8QdcJm97m;cirQ`fpxj@Z$PXYmJbrHG%(ldz6=t$Y=17#YpHYa(<(^w"
    "NJ(R|)8rl&N4vr8j+-WBi%fx;X;K9mZ#ca)sawQ}#!7qI`ZzE(cutzMQH(ZNMw%imLc`>vDH;YGY#U9nFw`)"
    "(Xv<BJn=hPq=C}6F6|Y0HZeDJ>9F8qc3vIbKb2zF<9kk`<&f&0PHqcb9M4Dsukc>z9tbaRsoaKjH+U`D@GI0"
    "aPWTz>w71ACUcgI!Jq<yr&%r#BJjy{|on`Z4(MPs#T9uYivj++)SjXhY#o0jdf!{ond6_g)r8&2z>@G!V>+B"
    "?&>AB<f}wrLv$W1QxkAyL3Z>d_e*1zpTmoniZSjt+^pH+l1@d1Er_%-$AOhxFSTS=l;SU^bmmLB}3Wm(J+cp"
    "`tPAjERRGJde)Uh(;bPi_VhP(P47vEDebdwn1lENOl<fIm`FC06%2rDhP`h2;(&8tc-yzQg6<x7~o>I=4`DL"
    "-OXrz`#3m+=XST`tcj8{CM(W*t)J?n?TiH{32BWeEbC1Y8f2`YwA>_Nt--`%wMoLmGXrO_Ng^T`X`rk%Nn~r"
    "t5bOZ>icN*VmfV9Srb7^e{s8`0eBLRyMhH6;Zj;1@M2pfklaz#HiqkHWl!k<g*d~*-R)TFf&9j3^wzEklDUX"
    "mcB8yB?uk}w|u-!4psBDQRFnf%u0OJg&IYxC$DA8DB)PyDm&lsaN_$Y&Ai!ss?Fhn^s$!Pn~K&Q(jW9<_|V~"
    "H{EU|9ozT{6U&2nQCY9mZrhqDaj!roth`Y=tqs7nA022j`oRO?;3s8DXs1dgpMjW*H|Z?cfAvgK^saD8uQ3a"
    "n=q=G$t75fq}vEz_<uK#$Z`sT(-jtlLN+8>&##qU|hEj42}QAP2&~t;cm(btTq@{oaUDx!GI$5z61?M6tndu"
    ">ZQO&<MH8cNiX|L@HjzZvcQDja;HAq?$}^Lwucm$6(&@e(T39w6S_T`Xe=>dB7g?Z785pxu?EW;lcYUnnCvl"
    "0gK&dwkx3Q=8iGfUN$H;uYlN>$Au%a}vc+kYNf{I>QoBs5pj0tiW>W9V$vFhZ-sEhe<c!HMlZ_WbhuBz|s3N"
    "Tq1!k8i8f2{DbjuWL4JI11O!4r{;CW?AL@?4|S!GJLh76Narc?-Qu#GaMLombOlPR;01@a+GRzcQ=B#YA|(<"
    "CHQq#l{3A)#Wn$aK9_+5N*IQMS83rg?mjF_~l9Z{<^uYj>P6E!)8f%ofwC{ZWR~6VtjKl4uMuZ2|*>=Z0w;d"
    "W^xc!VGDL7bYLf(AJs3Ho**Q9T)}&%=jn8YNP8?O3a90TydITMg{|l)cG<h7*Wi&m(lxhWDcpYHyNAg7-KTM"
    "Orr(PAq`fhX-O+IfmvRbwm!yiI$oBw0uqhkWqDX$@cb?-0*^3Qc9)f{;KJl~S=BZ&*k+g2ZS%t5by?GN!Fx!"
    "0RZz8|sG>Bw6d|E_;`F%`p`m~xwz(9kmG1gJncijnK{Eb&2zl*ncPYZ7WsS)8Qp9VW(+qEC>@P)Rt5jjxV2Y"
    "?<V-BSqrigA8C>C2x5fcYDaQ2uYHj)ts$|h5kw2BRpU8X1vg$}fBrYH-=4a>9l74^=GHOAPX*qEXsG+vyh8k"
    "M2BA~n~j3Jn&s$wu`ajh{oL>`l}rR@9g*HoA|s(jj10hRR5rP=Oh2jD{L<IQ=!o+T@AGUSm88Z1CJQCgK=xu"
    "*@|k+oXocTVpB^IoQ@3(}A#|an_jG(@OdfQmcS#1H;8>t8o$-D^gdD)4*IYn`*pPaxI^w-;=&uawK<?n?rbQ"
    "cVmt7m~mq=*SObGsWIX17;Id&4HlTm##P9Xhtp`|x^1Rt%r<VKK?l!p<2Igg2g`I5q-}C&>?1(~qC;aL3Ff<"
    "O+y}`n<+B8jfG|!kNr(v0BK4DmjDRg>Pf4iyCdX*z=p7uT-5n*N<AsgMPZBkjQMLJY$4wHpU97;oBuT=JI-E"
    "|Dr0o(#<0DBH2{?EzlH{?BIanT&6zy`u6y1_C7(3YRn^eKTq4D0NuD8T)!m&%vn>4}s;`H654GtEm>!wI>vY"
    "0(LMfYg~9qx{NlQoN$H73VR?Pb+;xIc1@s%VueFuzU7U}Fxa-KJElK+(8uO2@$sp4p~MBqI)%*QRW%*f3dbn"
    "uJ0J+iBA@6gM<Rn`Zl3RUdAYtf0$7)5YnsX%U($Qj<-~&|oopY`Rvi?cd2fnf51+ML%=@dC0cyZnS9~H*idD"
    "oAz2Z?SXN3EH`c2M+?k%GbHTj!)d-5+CEh@4xC{T!GmYR86MNvgXP5;(LOsgu9A^K;i0jXjQ7K=_rtPFIWD8"
    "4V2smcGA0VRNUbJgqo9k~Z?dG0{c)S5U-RiW|2{idO}kr7mPX4Olgnhi7FEsgcE@0{yj7~eyd^7wjX9jQl9j"
    "CjMdK)06$dwXW|GyBj5t_6k~OVjLt`OX8;Tni_ec{G3gN=cm<)%b)V()Udftz`ZcMO4wwop_G+vaBn<hLoSD"
    "a>>CL%Og#6FuQvX1w2<34GAu-JCC*fdcwVn*byY2vj^s!g^t#+oLkEugSGHBD@Yk%rPv)0DKu5{r|jDGiPco"
    "Qb9>3qQ_4`DdE)wuoV|&NLMPkfCwSs0=_4gJDL!!)i_7b;&EEIv`n`HW@Vmks@`-s13*zvpL3Uh0q?{Vq!d>"
    "eH-Q94;I<(78#@Q!p3BjF|VaiL%!Xy%NTDLD=^cHiEyJ1r*+0;yF}3#XiP-{4xWw1bSz^ImYK#(yWCbBj(MK"
    "mjz=ljCyZ%kgRw*7t#R)MP&en;C8LPb;Cyk~MVti(i_|pYJUCg*))5zV+>GzZk3o`tJ=jLO+eTc*2^y1S#Jy"
    "Hb4cK<aF5<d9q`<5qZo-T<oHh}+?a@SI5eX6jG<f!qpfQX!Sk{nW?J--iHg4TI(Os33F;q)K@F3h^C)9)p0u"
    "4jTN_anBx*5JM*=IrpWsB1~6FMkVq_&wbL8)T4%!I9VkKAN(<36Lf+p|BN9iIE;VVy}5H*icAn)JJCr#a*9S"
    "ZR{Aj~1AvCVAM=htpb<qJ64pEH)`4f(OrPlPadM2g`Dkx_x$NEF@`y!b20uk~Szu43?boPPOd^W0$NsMWbMh"
    ")1p%>3b;tEI>n=)i`lYMwNh^R!(_P?!ZJ~Ku<UlX?39cfI3~+Zc`dQ_z_>e>ozm^21!mbP6L$3BwCt2^pDG&"
    "5PLqh>!L#f%jcM$`vg|Z#pB<)~C^Zkt4~=W5y&p!sAC_IRoV1LBF;2@#t0>?iwVbq$f-YvuNt-$j$Sl84#&c"
    "Kq%+lM*T{1m*PP=<f+D1+slj&r<)>!+1+#T1+&;V?K*-nN99)CD}C&L3^MPob}5feRl&XbW*jXqe`lTiWSVe"
    "+1g4hs;C`DDEBJ=zn@F1b&}#sL|p{bWfTbdmZ`md1e>vjJsUt>={I(}%nK@6r6n!Ft-=da^uP)|gBu>-TW(M"
    "tHkpH(A*#RbWPwRl&v_PK(LvR)L~1m#m3{8$4Ud+DJwmC_~8*(keDAcODu-Lvh36AQ|HSIPInwJLDZ1!b9Ul"
    "=^7a#LUYCG7a1Z$gGKBV8M4gU(f-n4DOR|PrS8eaKhLkO-74Z!F<qSf^XRXu{j7?gKPQR>aar^dh^`If$!Y0"
    "dUw*lAiY~rfUcaTi>iBg6kJGp0zx?9o(?>VqFAjeTMd{D)aWHi`{CRYCdUAB#J9#Bixp*zp__O!9EdE>m@P_"
    "nnuExpS3NGuvKzK;fudWu^4#2(R?((gV{FC0VPH<5ay)G=v@|H(bl=xXbaL2z_Y%Py^r^J6cXjh4p)RH)J1z"
    "px3xV8V+(mThilh)E%n55GrbJvd1WZWMXlPV0ktd`u>hi!n6$?N?<U)JIAD-=EoSC)cqth!=lw35asYmI01S"
    "EX9kRJ~>#K$5I#O5Tj!KvJ!1tDfG^Kq@x16({!~1>2f}E1&!XNH`-kg>P<o07*5isd~G22}!+I-BsBMEvpUE"
    "1g#Rr>#hWMYhspFN7TQpV&PSj?b3g&Rv)SpR3%yNMRKWi7{6$>pYyzBwGpFY?YEvPIi$06n&;!<qB0vN53}3"
    "Jy#J65D;8OC+oM%j%zm+Clk{&CTx<g_?w4_&-%qfN5l;5N0?WYQv;mBNC;h>6l;ao`tO1Dp?>v2+yU*;qPq<"
    "H}KTr_i4Tx-M%EN~u8bEn<`fxFk4VbLQ3*ggG4QM=9fhz?#3MNge)s3J8d>l=x)am0G)hyGbGOdn!fM+5|Gb"
    "Y)i`$mJuapperJ{gaO`D~7Y%d%K?EAgY>TgbjFKP8If%c|fd3RV-vyQ!B|)e&W0HVN>01a{54ta1pMcG(mzb"
    "{AfibXgS^x{tmoPSy~&o^gp=p{k*9OS&a)p}L09wOmWw0!^JC;)}-|w>G=Gwl}jZt19DZ2yW<IZd~fEcP+&N"
    "MJ%l)b~C>MMK7zVcQv^JMJunVbtSU`MXIPJwKA;&MY60Vc{8U1MX#!*cY8twif)hEsWO|gsxwG3WmA%=w_e*"
    "Vm9nZgqD;yr1&_LFmpn?9?ocs>4Y7*sL2N0B61QBsk8@kOK8Q~%^I7}smOd#jh~jo9H-Yed0(puTz$PMU6Dc"
    "Q7U{jEdDWDh(sy2p|lnHDEx;BEUd<kq8rZ$T$$r9K&Y;7FXnG(=6G^tHvJxv0e1Fg-WI!6MVM*WI&BS8WiME"
    "y#$oE?Epp?-B)of?5nLpGtYB`*S-$g<$%PZ^q*2uO)Z`LV@mKhJJ<d0z3$LsfsNct$JEhps#;S+doE*aE(5z"
    "6{WW2MFp0{4zZoo*uwko=Xgun}P*k826pDWkxiDDFnq$xn-;@5LT%7_m=7L*yu%nt8kg0h;H;neb;dro(u#J"
    "@7-l(zA6sB7;efcgVk|>MR6}vnXHL}ES_6|%6OM+9kDQWK$#ek-91yH`m|?Q+f;!DrOW7OXmnsQ*JW-jG&f+"
    "m*D^gGgkHSS$}$}he3Ag4(kug&L4ZOGe3rSXxVS}~9W9g7k&U=$W2j|pCI~j1k)$$N8v|MBd7f3ImXH{bf)B"
    "%#nbH`T;z*Q~!S1I^QxQ+>dVInX9tJIv^n@iMI6tuA2}@*fc)+t0mZ&fY;Y24a(cxzgbjb-zOc<&df)ke5$l"
    "!s>O;}1|8+%}46PD62?9e`bwoI2rK^I$Q!crauS9FmHOGOl5aV91#-AC^xgl!RVV8T)r0xE*Mgrzz#IgoJ)O"
    "HE*GfYTC|+7R$yge5E^q2~uwSqaN%2%gZR5|**3uz^WRSjOWTZy<samWdG5P(G`q%vDCf6;(=tsR)ST3P~^>"
    "0aAn+3Fh06NhkLY$#|5{`nQwES$=rZ#Cv8Avq1@CNlb7O3r4uX2~J~S2t7Z+Sx}OIVicT5Gr{0XQ*aR!E{ae"
    "EmvJ(Lm95|^(vb)!V!?G#gxz>Ph2<tD%IFdo+{T0#U+_YZnD`>iUkJZ`P;{0^%rOkX0<y)B#t=LXfG|TDLc~"
    "E2ayCQAfDC~|G=z#|3c;1s5IP`M@PQ3sVx$Krw;^nlqYp-WLy~~-@IURsN*WJeRA~;$;sJ{*)FF91WD#aN<U"
    "YGYho@j{`b1KO1dJuwAyp(4;RZXTjszj}T!%CvK>~_(NE^%Cf-lvfNJzFQLLG`mi4az%L$Nr=A)H8u;vork<"
    "JlQXM8p_fo<qrq=;DiWC>0T2q-hSdzn;<InHp<oO*pg|vK-1r!4GDXLz5`rK~8dL8V(_lActmg%pSOM9GZut"
    "3O>f6MTGF+q&T#UaqPheacC8e9sVbAXdMk*R0$4kqTz}wz@cq4U=ijwjBg+4W;DNj92_3W@tz}OXb7+?YsPt"
    "=-i}8p%)EwS!RaOUAi?p#;AVL`pG^C+hrD<LH?@PLC_+#K6wNR)^t6F5l3`Q`rYNEqMn{DXRs_SCxW*lv=!L"
    "N%$aUl)8kR%=7hTrE(g>*H%UD<z0am2h3hUR$iN&CZIa6Up`$*rkCV=#^tqja<#gu@8Oa(=@-Hw35Pq)txNN"
    "mEIuoDBWxP-Os(}W(ApoEZ!sDa5xIBqni(FP(I;h?ehq2Yh3h2zSDbH$W}a6m<Hpr}F+j;IVy6kqzmVfF2~4"
    "5xW^2tT}I**LDceXdBJRpH>7pzy#xqQa50LCImrK2SnQ`w-zo9~>VVcFsVTd~irv`$#ba9~?Oz5jjw~2ggpt"
    "H0r>_9vnE?KDchA9voX0oGrG{gM+GrLq(T)a8yljsyHJL4y|7^CJt93;=}_bjI>P@!NVgQHX0Bc$Y(}4W-K5"
    "x=z#}^kGG8vM&7}Z5g}&<RB;CfQ??Bge%gVlXqW-YIxrp2NCT2|V5V(kUC22w8<;AtlmjP$aU#n&a2l8=#)J"
    "cJ+*&#8%28p^aNasrH2DTDLb3xKZ{RW{I2`E)u3BdZDcrzy(CGqUwt<`0iQ<Sha2pReV95qSA{ujmf(?SU&a"
    "D$0XgIPgG+BJ92Eju!MHp%jA~aO2nFi6Y;d0m*V;!h!87PWGgV5oq0Sz<=6Al^tJcF<;(}NOckR;%QfGo`*Y"
    "0E6(hZ!V`1sR|$gXGbSGayk0DOx6meh&-BREA}WE5{&JSenRU3{r>1i7~|>y_+bb@%XS$#V)ptv_a6KNi!%C"
    "njhdqgQB6~Rc@^b3Ly)E5K^c?@!+!u!eE0EL8#&gH+b)P|H1Hp1suHhwEyU_2PovAbP#r3d4Pj5G2q3Qc2G7"
    "3t_Twkn#2GWYwAIJHfCb+Euv07Xchn}iu8l#VaWkaKxh#bTct|@2A&K64@wBabGQ3Y&IFhsgy;VD?RgS$cd0"
    "$MuW!?hh#Z`aYP<mnMHmu*S{EMiU}$)-xY7}Zg(r$EAz^rUq!?2YW*;4y!{Z(*TpS}qkVTV`Fe)%Uz!3?f1G"
    "594kT50$K}Z1!V?$3I2=ft^gn)`89${%z?0}^sEQ@R80SZT09s*rQ9s*%S1aR>sBdm-7E5cxeRS|H-nv1Z_`"
    "!t6KLaZ}2?K4G@im)~)IGCXbN=Q&@kTVgK(DvB@i9}Gs!cGmi5)qW}_Hlv_L{K6kq6Q}qL5YlMw84l&P@>w0"
    "hW{}Ul<44CQDq@0F~Nc2ib7CggA+xVgrNR=*xx@Ko)z()Y2*G+>sYa5AgKQy_xofAHvmEXx47RYIOypI>c7i"
    "<V|;+357d7z`@PN-_;L@_e+T=<L{WqusQ)hY9|sOr;(_{aMgM_g4o=*GQrkMWjywl~G7=gsx}*bTG&ECuF$c"
    "<6XsAdt4%Bxi`xrckm;(-!iI#z4NH<X5o$f!-cL0M-g+m59*WkO?{l|C>5r!IkceD@SGJr`m`0iuhioXzdhu"
    "L?p`j(u9$f40_#u<<x17|H0>%cP|DCc3J;>s{^5tb&h00Wm{abip_@TMJ<!!sNz+!(Igriv!Jz)e7OfTIiC2"
    "IK}lxgbc}^dJQn1PwV!Aj~ZY);3Tau?4}S;RY<VAVfSP4p3-8$hNU{<M|9!sK8+HB^HDZj1*yDL72cyvE~&-"
    "v%bpVc?@ejC25x@inM~H!Jq*RD@YcM80f5m<n3~U5>=2Q+%$kJsUT&$DB%Ycq>6+Xpqzr#v5YbxF$HPb1=fL"
    "QEl3*_DXxryB0*syizp}>lqSZ6g7>EL-zU?%tUpM`Uk{I2c-M#V-miXdbkQUhyf>%+2!w!>3*P(Fe+0i-p3W"
    "!J{_G(y-oQ=mFj1;dC?UleymzJlkQoJGtigLf`n`e05pVF`cmCt~0gE|!Z!q7&2m};&&@>djt~{zivq%Wz%R"
    "guy30j0X2rVLEi!~47xp93=9!J!<2+uw5+i^Fbbgex%xNpJN07K3MC%4_v00Ys6fe0l$;kmPYKo<i{h{AJE`"
    "&Rvnxcl6md)K$@Ttv=}$2RJKge!~)1Fj2?f-o`)wz$$3MnwS?S?a>*D5zpgUzk0$bPkV+s4#tu4FnfW_QI0z"
    "1OZ1cEDetjNAkk5KqMgrFD#E>GC`QTup$sw9I*>4W5Wk5bzxO>;}1~i!s<ZuI`XIqYvSOGFL7aQ9AxnYE*K$"
    "kphcRuU}P_ip2K4(*0Hi6STUq67~!GW!3<k4B0_@$pS55_24M&!YQcyKK3(8SS}>x65CtE!V8p})4o=R35gX"
    "Z<gAub}lmy|1|4|l<(iq61%2+VUVql6ZV!<en0V=|T1*5*rn|zjjPx`J_N$w^$heuqzYxPhW5HO0I0HZ1nge"
    "bBCjOu_i0p$f4HIZBz@nr@WwE@|p$PF+?VnhflJHQx?avZ|R4=~08670xx8yMs9Kt>lNU`)hA7h#xy$$0Q$4"
    "HPiH4pC%__^3k#Ob0^?zd6oK4DjGL$e9hs5lWMslPE5M$QtFG2BQkUS<YE}@BlT;IgfDc0co0Z5sY029#6oy"
    "j0G>Q?m1Vna7ETZ=Q<X!7`y1aUaM&2m`x8o^mpA2ZiC^)@~i?TNDK@Crw{~<0U(YHf?&Z|LP{VA9>uhRFn=I"
    "KFtRw(2SUaN5Los=s0ha(pyYwj!T5D0cOXnGi1DQkgpGwQ!pwmrvB1TeIFNN3Ly;IBQRfXL3j`L$V+<q@PY-"
    "BTLyGX|;3qYt3`7x1PD82)CJtmN4XFb$g`d%oCN^|{5*pG*H|~JsGZYC#t_$f5MdN^rE1RKM98{4dGZc>lE5"
    "=-g+E<(Buz^H{1*Bvsx@b}vN=1MWa419R2>9X1WGE8~C8S7(vN6mk2oo8agaV5rkfCXG{D9>#G>dNp0*Yg39"
    "tvMKc9LLPL;@IJ7DLNO&?1atXcY-ttVs;LpXSeDH;HwwCJa{;ISg%s!vh+_FeErR_$drS!$5=*!Z0lSoPjKZ"
    "VR#s%@FN&TL`Du!0>jAIMjem<hEZX_bzoZwqoY8JD}7;16i|_cFN}?XD#q-E^=;_<JDDfb{=~`eXYM}_&&7D"
    "x`LQ%8VjK|y%VL3uBS&C)P@a&&1Xe_IiA0zvurerJ9I*nc;)Dn+TVQphV-Zlmz?z^8yYkEjYh%KUE^ELEiHR"
    "@1zyT*TCcsEj2b}nIkfK}M#~eN2ga?ERzJ<<-hyx$^J~}5dAW0z2bWT(xmqJ_}bxw3ZuHf71oR}E#!Rf7YVx"
    "t^?FdFQfl7RF&@RS2iX*_^Y_1ZaQ@sP#UaOafAgBD@eom1bI(ruD{b)53=vqQuEUDJcgP;^oB+BsDb@I%pN="
    "Tt|4axv{shQm?nJ_9606N)0BoC2pdhDilqM1eCB3M`6r0%tTjez1ZGoU!;uAe>ABXFL?Xj>Hi-6Oj<cmqcJP"
    "60`_I2uwx77Hj^1`89`PvN@uT9xxMzEBw>}v%$&14;*k39Ac%Z0frw9!w*VUgR}6{2C|q2=V6e-PiSxv896}"
    "V3@&3EbwF|%T!jJGg-8b1QLx38#^5FjsK|mC+(tnaV+KR`c6Dyths+Q0i+4RAK?8urk-QKrEIJ&a3&F!u1fR"
    "DMA^<>0Q41jh&m9O;7D5GJiX&hlbWG@gWh;b<YTN;eRR|k^Tvrkml7xqgFH9k6c&Z3<6q1F<iZwzZ{TersxD"
    "-*RC!}Z{Dg5Aslp&$P&rC=ak{{@}gw(C0gOZexCg|*dEF>Xq>ono#BNT}T8lY%|q7jWXAgKt&T4&aUK!oC<v"
    "Es@?C=r?`vKWMtp@CvdKq%ji%Y!RN#`D>?QU3jqgLv1tQ92mjm4)9tPjAPgbY*i&NS$b9Vql0P7NKk~mXLb;"
    "nnZC`MA+fiG#FVNeSXd20|>0!uX%)H5Kzxwi(ve^()rgi7R31a|60Yu7GVlN>sa7o%>rn@ZckL}^{5j8+6Ds"
    "q=1Lt(f7X!j^xy{s3=KpPO0V0n2(Es}I^Ko{VhX?SZA5J70Cm5OjBeZk>46&+h+G#s;YP;+7gs;rm^i2+>xv"
    "s22Ud)|aqHU!`kwq4B<a^fU;JGY!_pvVSJsNR?y~Au9?2MFAJ?+b`~W{;!Sc}Xz&{<piXaFfWeBVcK6@a{4_"
    "Fn1Dvs=c)p5ZCmK(4pvats!Ghl5Hb{)wJ2ts1Oi!Li52#tX&zMOy{EC#ShGXjG6b#$WfEn?0G2qFSNebcc3("
    "$yA3h9w8{X$yj=u-G6!FF+6-03MJOg&-#I{D3P#A&3pY6MT9?P!bb1ILQe?X;kA4MruM(7Jym@5)*>*@L*A;"
    "B?J}WiQ-C12r9!PMVOKh)ccvulgW*H`(lTR{&Y4wq$S?9bEpoA7)94#P!kJ86ps=R)W*USdYVEo5)>t%RE1z"
    "Pn%M?lx<W7(lrD;tg<w2Rh_KQYf{94SBAnEPU@|DfuB0!RiU~8m6b92V@kN-%U?wKOSW_9y^U2b&`6N-NGdK"
    "wd7k<`&(>U;fe}seJEDn;O(;J)zga{?Y!9^tV39>W?mjSuLPjzq=BR)Xs4z8mde?U?m+ytc8g|r8^@c_n^`V"
    "b@@vdGdOg2sauV+ut0c8>CgNq^$R_m__(Iix_|wT}c3iWtj&5<-YrIN}I`2pN<or1XMN(Oe}FrWb?`N*70ZL"
    "6|rp0!uFl8|hdClwOb|D8sI#7bJ}dGrshKWHIqYm|l=PCcs$J3(~LK6y5qh>hyw?0pViUe?mwV2SNA&2B`y*"
    "gwlJLCX%ZmvfjJ20lC8Oy(<zUK0v*9MWY;lKzi?r1*F%7-n-)Q0LImOS0Wy=$a?Qe#)B4P?_K$Jm1g;UGM>A"
    "6WtQGf?vm-D`Tnl^p>$ZrXu9vpL<15>`(4?vKq2+tHHqg+iLe2$X;{2CI`EoBN)cEKUh`N-BcL9<7GWWFr3t"
    "TPRGjg3;kAkiFv2#x)=?40+K1PEou~NL`cXIHwGBxZMJL{nND#tr#TyzDC6r#gVX<5hku~ED4+$22H{OUS`2"
    "lLj8yV*a1k#T;DkQ!xG~|trh%l~>yfG1>Mb?rxHX^ndd-B$|)AW!}AMWzMNAn+tZv49*h^3+E;@AtpvIzL$X"
    "un$?3MHh*yA?594iUE8tqcVgN3-3k==cF^vs)eC2n5t%w<Z+6uC&&zjf60|rn)2~614c*>5|Y$*dlGDOXAly"
    "ifMr!a|>M(9)>IUhww`xf|CQ^GnYh$fe55qE{O_%*~8T*mqdp_3cf=wiHVFHoZh%3Hnvd*qbn{c2?MSJ{cuT"
    "X6l_s-!X;%<K*iMqmy}0A6=C;V(&a)%zOH18_3b9#qqBb={dKjzCjRsEWU&mszWj3KmbDjOF0adl;mW%%301"
    "vheI@)jeGAFU*q%=x-J)l4_*+Zj(w|phY$$#rA{!9-^XTmK<mkG0^2(;NVBX;JXYX?v!?*n54G7=diZ63zW7"
    "dDc;Rf+nw?NyD!o8y(2;~Xlr1z_nUMv`2i`osL%Y!P4j4U6xm%LYGERTAp$ap%)ORTOdcIKA7S%2V`B45`(A"
    "a2E`uHxbXIdu!KbUvDl`@>?60VUkl6~4MrA0WYu)czsAtP{X!rJh<>95pwR-Q+%6xshi>bv$<3HvO!={xvTa"
    "pm<(i{N`O;0QHL|`lt6Z$l7HS?a4i4>8if;%Dra+BGB~_+>U1R$#j&AA?r8w^>1$^0p#FY+g0|0HgpGR2yKz"
    "bNrUz2Zta{6{Sozawo&n|`*!KbY~>KjA>SsR?Mr;AsRBkm-`lz0zTSydyYODyl|0Q^I?eNOaSfV{lZV;uWZr"
    "+shSdwcxV_D7faKl*Ws~%8OoUW3Lhg5SpWjcgjT^lmD%b{#X@=$BNq;aM<)}uAZH6TOJ5L|y?xXwegYJ{*4@"
    "7JPX~t$t=N%0m+6>RDV~LUuYer{1T7ZTNZ^q@h%v>q=G0|yQ5pQHC(12-J2~Qu#7-y)4)o*nu1PvYCm`?WSz"
    "S!V#oVky_PsXERKAU4Av@D3#E1~Lu5}t-*IY9KZeR3`R02_{F@lX36*Afr2;kc%sHqj5Q;kc%qHPH^N;kYK9"
    "*Ov~q;kbrC)JFib;kc$>*4Gca;kd>@d2)cza9oq9JtZP-IIdCgt@~g%9M{C#eTfIsa4gGLWN+slK*MoOyKl*"
    "dzu~wBi0vH^_#V<4A>ND-mWJaR6|o;G*anMih6P2#aSe%Th6Go`agB{`#s*!(aSe}Yh6h{2agB~`Mh8#BvCK"
    "u2W?Zl}9M|YHtcVdc9M`}!tc0;N9M`BctbVaI9M|ZG#&pm%9M=df3u66r@BBXRNiW_njvLa82MH|IsvXo;-*"
    "~>NCj8y1uZpyfqRqR@W0hrmWNp2LJXUqqN8QGK;*qL)+W&9YAJ>Xk8LJ?=gP?Z;7^~7^YCpY-WPTzyQWo(m<"
    "bDdPq7?Cq6uu(Md5QQ1N?(DcP(=Ltl%KvW$%puLYCoM-@rL+C8b6V5Wf<ZYyGu2*C8)3}E>KG0MTaGxOQBuU"
    "2&*EaiXyy9-=)&oEpkvLHeA#BRUvDla4lC5t47}FEN&^tV-hHKT4C&(5GXG@Vz+NzDagH0c<Jmy#i0B#toQn"
    "%O4s$JgFo%YUwf%*4^_AI*InJ3hY3It;tybJ{~am=>d#<XlN~Ao=8s^rlMYop_ZPpdT@DpNjT*3`Ck|D8joN"
    "R$@eNgaje2iecN;1KT?@h17B*B2%M#G^i>~tSJ>25xSh`H^yF6*JyK7nR-bN|tX_J+m1a0Zt#;J?$u4}Dh8>"
    "b%T@gsV%hT@dM-<9%amv&WDJP!M-JmDj6tv?&PHtC~nvojmJJmn{EwI>_9H0>vCr5hW&GUKDHvJV@(LhGY&v"
    "jZEsJVt!vZSTFtF7y40q_XR}Dl16O^;N<9HHd$|byZ$O-PTvp?^i3kbXu!qhHBBhY7_Ofm3pjks*?@QTxAIz"
    "k2JNzYGAj%YI%tex2f=|U++QxQ)>+-0OJdw+)ITi+SU~f`BZb_t9qq>3R5xlRb16Mg(;o+O5f5mg{hwVs;};"
    "r!W1uj#jp2CVak@iva36!FvWY<aT~o+n7X~|w{lk$rfBb)t-2oyQ@pJqeoH45B!H*Q5N!Xc4^yM@@$}00Jg%"
    "qC(^4n()VbCHfgzH4irj34z|hIPb*}bAU}zNH8dsVlFeFM(iIrUv7*ds|)XjDY44v9j=k~q{483L-y_F4=HG"
    "M&PC?zR&7b*Uolr^0ZHB(mA%bn_=U0Nzz^oBC$OCo6_k)_^B%raTCbNSL_iCN=LCD5+jmNlWVuU<+Z#C||Ld"
    "GU}*$QmTd{TImURRi@1PQI=of2AV>S-Pnqy{a_>nSrgrU`vk%Fa||xV5n}^K&C)zP+0HUK-SM{=&x?yK&DWq"
    "Lfq)%Kn75!GAuWAAgixa4OVw{AX8BFC~Rr*Kqj#$5C!SK7-=3})yr#wx>YZ>;Zl&<O_0B1wV^g5&uWc<-HpL"
    "6zSUOMq3n*b)aCQu8%Qm=SFwwwP0#C#hZVb0wwuyjovaPHv0tOtaPWQMc(OF0^ANRolwB?83}hn)NCtwc1!2"
    "YEf(}5}0#N01K__8qk=WvPL5E>$!Kn7UfU_{97K?S~3pxc_i$b;k1)W8`>T<&cgN~tIRay4JpfjjfNme^z&{"
    "@d3EVlS!(0MG2OWJ?7wf5yCJ-=UsM$)s-w*;Vf0<epXv`um-yP>SpG}Bq~km6Qq8=lb@11V~m%ct$XD|1)(X"
    "kBaUm$5Pnx+fM-jsa{KtPYH_Zv-0vZvp_t5D;|;tT;xn)yq2StL!4!7*rh$Tf8FJBy=4T)kYC)5T*`@b(aV>"
    "0$T?`wM7IOgeJA>#|?i7HiKGqW7!;n4WL%7SnUkK2El8B*kTL8hOsOkSKXTHizna5*A#hui&$3CFW=}%tZXQ"
    "@<c!2GS2aDiFZM|MT9<Vnf3_^0(6t}%ZmZ#E7C?T>PbYJUz%)hhv>t}cz}96@wI&1%V!5?I9T4kF0LTz%T?o"
    "~S0LUm<?H|VaG5|6KUi(*3S_nW!AnGEhUJ5`)Ase9BvKWAjV_7bWmoK^~7e5`HpZ?anx_(iePy4RkPwpl+{m"
    "C>Njgz}yh!3G(znq^q8lQTfe(hae{c-wvPne!R;+2D|E+_MKXq$&U;hHD2ue$%vp`|#$(-(cBr|Z(A2YPzBk"
    "3=1M>_AU1_o%2tk00pi<rWxq=!pY8z1&%&4n29Gr<c2W)S;&i^z?FjkUI4Afu3G&KvIXEIndL~4NmINvj=*b"
    ")n04R!(zkXtH0PdtTr6J+N%wESZ+9cwU-<8u->40xu=Yf#n-+#KR!J>J-R-<IN!!__BbfIWWP-FbTUpyccY}"
    "(0l1SK%fOy!F4rHbfAsnD+3E2@(D6kf?f10@p<G?}KFgKE-2e3Z<<aLq`n^9-PkQIai>Bvzo=@FK?LWKT#p`"
    "<Rv|OJaznFT<SDhby>Ro+4I$mpk-b|B++n>kDeLj0g(zjZmizD4XW{-DoSa{2$s&DVuRQ$w*HDKZn@+MNeW("
    "$vLa<dZ+cighW@W#C!n}_U%gEzpjdOOTBmj3nSm#gdL&hl%w!mN|2dPx~jI(eMF<@mnzU*^+C_sduuzMaw9p"
    "LgRU_QdDUqqEbKqwC(uE2-)mhSn(+n@W_Re9Ir+K=LiQ8)eBn&t3*)-;J)X$;}&C0{4!-SY!h%uls?o+%&W<"
    "JM^yL+oh|0NS`I+B<l~RdGd7`Jc6TN??b=)r1><-Mrko&_lL!$3m68o4-BjO8NiZn_i_Pvy~hlU^5uRxHI=`"
    ")jm`#xcy>J4PJ`};#@c2(AW^XWpxE4!2P6%?FB+>G_kbiJ_C;c4dmoS><bFU@ZUO`nh}sX3&8>n!($M>%v3)"
    "}!kW`w~nU#-n-JlTWQLbAB+dZwgcYHv{I*phf^}3CbY*K^n^bC(}RDwM-bSnz_5h%)!`G5n1eaN`jzDkRi#I"
    "pR<{laUzKD}NsD36cMo&;R2_7WzK^Wp=(j>g$9mB!}T>G?m_E-}X!pFSU*|MmJIm6w>8zdlpjcv%Tu;o8kd^"
    "B-?;ODFda$@oYA`Oxj~Hjh}vSEcibpV$|_;_xqL%dP}odaEjz0A&+^c%gkcKmF~rcTxuSKgB&p^J!8PZnK~M"
    "^I!k^>FVOk<#F+x{XgCJy}10#z4Gzz|7SLNoTmBDqwzQQ5tGI32JV~w>!-`!uU}5jPWsn>eJ<X*cYgKdYS9x"
    "ryEr;=Kiq3~WjJ$^m!H@FwZQ0(`P94Y|F-x|A6*~+(eM3t(dhepd2#aP*!|_Q_uG<A@6%~f8~)b2T%5j(<Kr"
    ")v%l};b`Lg%FzMNk6`sWwty-%O7i?4chak=FcznxxQUH7kgPL@0Fx1-au{_)wxRqv$u*X70k?w$8Hq&qrPv4"
    "K?Fq}h;5Lq(GFJk7-*9ZE?APXtSbdPszjDlvnUNG?@2RAxvdF{vnWA!I-mwUj0*Pw9|mLb<tfn6QKmwfh%$m"
    "rObs(1Fbi7l}$!N^;8wiOFP=<x~{I`uVi{addui@!M~ulX9DkGq)(id`g~W|4-4n7}{J@NmG?)GAAib<uFTy"
    "W*Os_DQRS$DrS^Qs7Nz1$V@`4(8+*hgzK!ZDD{vH9e6X8Ob!yAI@ol;xgv>{L!MefsUn<NH5d$}U^bVONvbn"
    "KITyw(PssX=T%3^nK5@Um^ykG75Z4J=e*4RdFNHV!{Nmz-=!_1vl9r_Imyi!QW15>hq1;f*rA`yYh$Y%k>3%"
    "oXaKi-4X)cGuJXdU(xc9cy=EFn{i91&|F~dAfjdVW-K1fsd>!Z0H=$tZ=ay3i_%AJ3b4Tpo_V2}@n(qxMhvT"
    "@~gC*<YwitvQIPAC=UB-Mjd4$NSf8*7Ztv%x@cD-?H^q?{N*2SQt^-FT-a6P8hCW$w;omhjY_sAAfYjLURD*"
    "+4Uz^CV@I=Pb<`O^C|%Fin$8a+_K`pqV6u2_vk?q&wU0L~61)Cyq`r_g=1^lj4pocV4C#&&i8kcS_vE?@IsP"
    "@#)pFH2H}ROhOYw63duPNy1Dj4Kr2}mWXsna$ypa<{5SWllv7Y>1GOUGfFkjm5@cqOHnbf!jV@)nXo)hrBIU"
    "T#LYdFkRj8ATIEg!bLWC*gN&#&6B*SB6YeAO!Q!O!&M!}o|M*n+E-R-cA5TZ=?Y;ZGpgu7t7oUz!&&$6SBc*"
    "gq9WKoi%k7ZTR439}rgd>e6G9EF9k^qL?rKJf%oAm4nla6i)T-i!i*)EF8)Ngt7PAj^XH6>Urgx?@cj+?B4i"
    "YyD(P5@VrtE;^8F#-lo0BZnGL>|3VwMq}m2<L;?I1iIn_;Du(=k+TYUb|xu7`#&BT2#?OCh-%@g{K<lDqo26"
    "PCJ54;>__$cO1L9}@S|phLq|@jDr$?)Q)y;vgqhXWZ3rt{%&!E75F92U$k*B;$E5Wa@rc)VM0a)m#I@EF}X*"
    "m#3r153QVxA~S^WRII0@R!@Y?+$65JD}|EG3MP#fgQ2o=pt!sACqt$(MFy!G%c(;=WUk&C4z;U_iMwhI7Nr("
    "9Nt)$WDM`{n#wAl^=q_-a58XSklnptRNuCI5hJ#$>10(c6<}A<hgt(7coCt^Qx3kmZ(&fr6Y=(EE6y?S0Mep"
    "(1(ZU8@IVUtznazs=DwElON|w0ETp2rLJk=_7|1U{%w}x;(mjNvvxHfckZ01U3cgb;UiDH$I3<+^%Etjrf9&"
    "oCS8gMsbC$5kd?##KO%}pLMqbjE=bCZ?(IGI{kEf0s>o%8JJoD`X{(g`UNV<^wYA|1C`WDT=~T32Yfaq9-PG"
    "MpM$YLTI<xCWWKst`#9;lvGZcZO(^E@p1GGBIur$z45LtlQOKsNJNaGUEP!F?2;+!UtqP2p7ZL)zR7x2i#c7"
    "X-boP;I3fqYDQ9P+-#-UawSsa)K*SJky1l=8VZ%}dtEQvMJuNv&D?ZKGUAp8Zq{{!RJjX?E01zFySm?i)~Q2"
    "it^0fK*4S1HS6t;r@QlgK%_7B3SE+FGv-|OAVq}uK)gKjXkf*~;QpvT|ruc~np-rYzLJU#XodEZHO9z4xH|s"
    "A>!`cRg?o2!dW)Pl?az<#ilyVEbp{tEVrgh<lxOD~{GAT7plrnngelNmZdJ;!>z}+NXOd)QKC=%(G4{Y(9kV"
    "C7oG$FZLcO+UF;fjD%XI8t@!v;hQjeDhA#u!DZ%$1vhOv1JMzg)R0SuCC#u=!MccRF7BiU?20i{8ysqLt9hj"
    "bS$rxY>M2)i4{lb+_Y}<!0c(lFXI2#x366N}IZ)Waie5gQBM5Jdwrk!(Gw1ThF_ToMdh#=_*=LEV~uu(lYlt"
    "J9N`T?$(R$(&XlOH-A{=rt(Zmk+~X6E)A2lC(;#u$y2Kn;R$(d?XH}PY$)>FU3`rDHDvCxC5yFz&4o^EmJST"
    "nd0tewxh@Q>L`pZmSm{byx0Z18_@G#pyX%n{2<0yRj1%Ez7$%A}3Uy~7%}JU_YTZlSFDF+-Fjp$Omy66T2M0"
    "QJ3&vzw*={^It*CEb8mt(r+?A&C>fDhkS1cvgq?wgWxN+}(0cm0WDYq(1T!rKo4BVZhVOq@E?(&szH~)&<od"
    "tLCDcoI_arcWLiDZ13yHy5Fgqt3QMcJIW1-hGr+=SyQGk3}d%B?5|ZY@3_ZtW+nTbrlp;)JX{pv9eyr#2qKQ"
    "}NQ~UpWtMK5&ZxK^-kugYu%Ra%GXs1hXs|@Z3$!)D^X^vL!rsbyBf3a5a?GoVs5MEvni9bJu<?NSeCkM9z%6"
    "wz<m^H>q3OxpS#A#og;Ucc(%s_j{3Ubwe|q<io<IWb|ncf4u>zG?BmFb%b&@ue|MQwbV@rp>B@WsdZJ;aKMJ"
    "vEu9H<t38?zGN#<hoKlt#Qf-7J!$eq8)KKZb9qmSX@jDT21{dz)$8u?;Ni<1Zh2?%@nhlw&q_q13bJGZ+ZmG"
    "iEzew?MZkCeTy(*Va7p~IHX{D>z>WyePFJXm>>;A_5W<R;<Tv#LA@>*HNg`0ZaMa`{F5}h-a8h1XZ18A&rEA"
    "N4E6Gk#jGD9<Wwu*gO2F0((O+&=}@CK4eH&rBZsFGr-k&0BBT=J9;GBGTYge*;qAD`m6o3mVH?bi9q4i{&q>"
    "XtrtYL@r@{d}L_5Ax~kcJzSETD-Wr{{6Cd^>Ux&DuCkbxOH4HgEKeM#n4@Uaz#nv$}vVwGRSD+u2-onjA8E9"
    "_QRAKS0mejtJY*L%;MH)L8x-2u3Nur=GKmxc0UtI6mdm_U@T)qnZl=6Lsx1mZHhIEtEY33yMn{5Pu+_BfBwJ"
    "!AF|B}$^"
)


class UsageExtractorError(RuntimeError):
    """Stable fail-closed error for Task 7 usage extraction."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProducerRefV1:
    """Immutable producer identity and value binding."""

    producer_type: str
    producer_id: UUID
    producer_identity_sha256: str
    producer_value_sha256: str

    def as_contract_tuple(self) -> tuple[str, str, str, str]:
        return (
            self.producer_type,
            str(self.producer_id),
            self.producer_identity_sha256,
            self.producer_value_sha256,
        )


@dataclass(frozen=True, slots=True)
class ConsumerReceiptRootV1:
    """The exact consumer STEP_RECEIPT root used by a usage proof."""

    step_output_id: UUID
    output_object_id: UUID
    native_identity_sha256: str
    native_output_sha256: str


@dataclass(frozen=True, slots=True)
class ReferenceOccurrenceV1:
    """One typed native reference occurrence corroborating edge consumption."""

    consumer_receipt_output_object_id: UUID
    consumer_receipt_identity_sha256: str
    reference_schema_id: str
    reference_schema_version: str
    reference_schema_sha256: str
    reference_path: str
    reference_ordinal: int
    producer_ref: ProducerRefV1

    def as_contract_tuple(self) -> tuple[object, ...]:
        return (
            str(self.consumer_receipt_output_object_id),
            self.consumer_receipt_identity_sha256,
            self.reference_schema_id,
            self.reference_schema_version,
            self.reference_schema_sha256,
            self.reference_path,
            self.reference_ordinal,
            self.producer_ref.as_contract_tuple(),
        )


@dataclass(frozen=True, slots=True)
class UsageRouteSpec:
    """One literal UsagePathV2 route."""

    route_ordinal: int
    rule_id: str
    producer_condition: str
    consumer_step_name: str
    consumer_subrole: str
    usage_kind: str
    receipt_kind: str
    census_schema_id: str
    json_pointer: str
    native_reference_relation: str
    consumer_fk: str
    reference_kind: str

    def as_artifact_tuple(self) -> tuple[object, ...]:
        return (
            self.route_ordinal,
            self.rule_id,
            self.producer_condition,
            self.consumer_step_name,
            self.consumer_subrole,
            self.usage_kind,
            self.receipt_kind,
            self.census_schema_id,
            self.json_pointer,
            self.native_reference_relation,
            self.consumer_fk,
            self.reference_kind,
        )

    def as_accepted_consumer_tuple(self) -> tuple[object, ...]:
        row = self.as_artifact_tuple()
        return (*row[:5], *row[6:])


@dataclass(frozen=True, slots=True)
class UsageExtractorSpec:
    """One immutable usage-kind extractor definition."""

    usage_kind: str
    extractor_id: str
    extractor_version: str
    program: tuple[str, ...]
    accepted_route_ordinals: tuple[int, ...]
    accepted_consumer_sha256: str
    extractor_sha256: str


@dataclass(frozen=True, slots=True)
class UsageExtractionRequest:
    """Complete typed input for one independent usage-evidence derivation."""

    tenant_id: UUID
    deal_id: UUID
    run_id: UUID
    pipeline_contract_version: str
    pipeline_contract_hash: str
    expected_edge_id: UUID
    edge_tuple_sha256: str
    route_ordinal: int
    usage_kind: str
    consumer_step_name: str
    consumer_subrole: str
    producer: ProducerRefV1
    consumer_receipt: ConsumerReceiptRootV1
    native_reference_relation: str
    consumer_fk: str
    reference_kind: str
    occurrences: tuple[ReferenceOccurrenceV1, ...]


@dataclass(frozen=True, slots=True)
class UsageEvidenceV1:
    """Hash-bound, independently rebuilt usage evidence for one edge."""

    expected_edge_id: UUID
    route_ordinal: int
    usage_kind: str
    extractor_id: str
    extractor_version: str
    extractor_sha256: str
    occurrences: tuple[ReferenceOccurrenceV1, ...]
    usage_evidence_sha256: str


def _normalize_json_value(value: object) -> object:
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            normalized_key = unicodedata.normalize("NFC", str(key))
            if normalized_key in normalized:
                raise ValueError("mapping keys collide after NFC normalization")
            normalized[normalized_key] = _normalize_json_value(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize_json_value(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        _normalize_json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _fail(message: str, *, code: str = CONSUMER_RECEIPT_INCOMPLETE) -> NoReturn:
    raise UsageExtractorError(code, message)


def _usage_route_from_row(row: list[object]) -> UsageRouteSpec:
    if (
        len(row) != 12
        or not isinstance(row[0], int)
        or isinstance(row[0], bool)
        or not all(isinstance(value, str) for value in row[1:])
    ):
        raise RuntimeError("TASK7_USAGE_ROUTE_SHAPE_INVALID")
    return UsageRouteSpec(
        route_ordinal=row[0],
        rule_id=cast(str, row[1]),
        producer_condition=cast(str, row[2]),
        consumer_step_name=cast(str, row[3]),
        consumer_subrole=cast(str, row[4]),
        usage_kind=cast(str, row[5]),
        receipt_kind=cast(str, row[6]),
        census_schema_id=cast(str, row[7]),
        json_pointer=cast(str, row[8]),
        native_reference_relation=cast(str, row[9]),
        consumer_fk=cast(str, row[10]),
        reference_kind=cast(str, row[11]),
    )


def _load_usage_authority() -> tuple[
    tuple[UsageRouteSpec, ...],
    Mapping[str, UsageExtractorSpec],
]:
    try:
        compressed = base64.b85decode(_USAGE_AUTHORITY_B85.encode("ascii"))
        decoded = json.loads(zlib.decompress(compressed))
        route_rows = cast(list[list[object]], decoded["routes"])
        extractor_rows = cast(list[list[object]], decoded["extractors"])
    except (KeyError, TypeError, ValueError, zlib.error, json.JSONDecodeError) as exc:
        raise RuntimeError("TASK7_USAGE_REGISTRY_UNAVAILABLE") from exc

    routes = tuple(_usage_route_from_row(row) for row in route_rows)
    if (
        len(routes) != 1_756
        or tuple(row.route_ordinal for row in routes) != tuple(range(1_756))
        or len({row.route_ordinal for row in routes}) != 1_756
        or _canonical_hash(
            ["usage-path-route-v2", [list(row.as_artifact_tuple()) for row in routes]]
        )
        != _USAGE_ROUTE_REGISTRY_SHA256
    ):
        raise RuntimeError("TASK7_USAGE_ROUTE_REGISTRY_HASH_MISMATCH")

    extractors: dict[str, UsageExtractorSpec] = {}
    covered_ordinals: list[int] = []
    for raw in extractor_rows:
        if len(raw) != 6:
            raise RuntimeError("TASK7_USAGE_EXTRACTOR_SHAPE_INVALID")
        usage_kind, extractor_id, extractor_version, program_raw, accepted_hash, row_hash = raw
        if not all(
            isinstance(item, str)
            for item in (
                usage_kind,
                extractor_id,
                extractor_version,
                accepted_hash,
                row_hash,
            )
        ) or not isinstance(program_raw, list):
            raise RuntimeError("TASK7_USAGE_EXTRACTOR_SHAPE_INVALID")
        if cast(str, usage_kind) in extractors:
            raise RuntimeError("TASK7_USAGE_EXTRACTOR_DUPLICATE")

        accepted_routes = tuple(
            route for route in routes if route.usage_kind == cast(str, usage_kind)
        )
        accepted = [list(route.as_accepted_consumer_tuple()) for route in accepted_routes]
        derived_accepted_hash = _canonical_hash(["accepted-consumer-set-v2", usage_kind, accepted])
        derived_extractor_hash = _canonical_hash(
            [
                "usage-extractor-definition-v2",
                usage_kind,
                extractor_id,
                extractor_version,
                program_raw,
                accepted,
            ]
        )
        if (
            not accepted_routes
            or derived_accepted_hash != accepted_hash
            or derived_extractor_hash != row_hash
            or len(program_raw) != 8
            or program_raw[:7]
            != [
                "REBUILD_TYPED_CENSUS",
                "RELOAD_LITERAL_RELATION_BY_CONSUMER_FK",
                "MATCH_EXACT_PRODUCER_REF_V1",
                "EMIT_REFERENCE_OCCURRENCE_V1",
                "REQUIRE_NONEMPTY",
                "SORT_REFERENCE_OCCURRENCE_FIRST_SEVEN",
                "FAIL_CLOSED",
            ]
        ):
            raise RuntimeError("TASK7_USAGE_EXTRACTOR_HASH_MISMATCH")

        ordinals = tuple(route.route_ordinal for route in accepted_routes)
        covered_ordinals.extend(ordinals)
        extractors[cast(str, usage_kind)] = UsageExtractorSpec(
            usage_kind=cast(str, usage_kind),
            extractor_id=cast(str, extractor_id),
            extractor_version=cast(str, extractor_version),
            program=tuple(cast(list[str], program_raw)),
            accepted_route_ordinals=ordinals,
            accepted_consumer_sha256=accepted_hash,
            extractor_sha256=row_hash,
        )

    if sorted(covered_ordinals) != list(range(1_756)) or len(extractors) != 19:
        raise RuntimeError("TASK7_USAGE_EXTRACTOR_PARTITION_MISMATCH")
    registry_projection = [
        [
            row.usage_kind,
            row.extractor_id,
            row.extractor_version,
            row.extractor_sha256,
            [
                list(routes[ordinal].as_accepted_consumer_tuple())
                for ordinal in row.accepted_route_ordinals
            ],
        ]
        for row in extractors.values()
    ]
    if (
        _canonical_hash(
            [
                "task7-usage-extractor-registry-v2",
                registry_projection,
                _USAGE_ROUTE_REGISTRY_SHA256,
            ]
        )
        != _USAGE_EXTRACTOR_REGISTRY_SHA256
    ):
        raise RuntimeError("TASK7_USAGE_EXTRACTOR_REGISTRY_HASH_MISMATCH")

    return routes, MappingProxyType(extractors)


USAGE_ROUTE_REGISTRY, USAGE_EXTRACTOR_REGISTRY = _load_usage_authority()


def _validate_hash(value: object, label: str) -> str:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        _fail(f"{label} is not a lowercase SHA-256")
    return value


def _validate_producer(producer: ProducerRefV1) -> None:
    if producer.producer_type not in {"INPUT_BINDING", "OUTPUT_OBJECT"}:
        _fail("producer type is outside the reviewed contract")
    if not isinstance(producer.producer_id, UUID):
        _fail("producer identifier is not a UUID")
    _validate_hash(producer.producer_identity_sha256, "producer identity hash")
    _validate_hash(producer.producer_value_sha256, "producer value hash")


def _occurrence_sort_key(row: ReferenceOccurrenceV1) -> tuple[object, ...]:
    return (
        row.consumer_receipt_output_object_id.bytes,
        row.consumer_receipt_identity_sha256,
        row.reference_schema_id,
        row.reference_schema_version,
        row.reference_schema_sha256,
        row.reference_path,
        row.reference_ordinal,
    )


def _validate_request_contract(request: UsageExtractionRequest) -> None:
    if (
        request.pipeline_contract_version != _FULL_STRICT_V21_VERSION
        or request.pipeline_contract_hash != FULL_STRICT_V21_CONTRACT_HASH
    ):
        _fail(
            "usage extraction is not bound to the reviewed full-strict-v21 contract",
            code=PIPELINE_CONTRACT_HASH_MISMATCH,
        )
    if not all(
        isinstance(value, UUID)
        for value in (
            request.tenant_id,
            request.deal_id,
            request.run_id,
            request.expected_edge_id,
            request.consumer_receipt.step_output_id,
            request.consumer_receipt.output_object_id,
        )
    ):
        _fail("usage extraction scope contains a non-UUID identifier")
    _validate_hash(request.edge_tuple_sha256, "edge tuple hash")
    _validate_hash(
        request.consumer_receipt.native_identity_sha256,
        "consumer identity hash",
    )
    _validate_hash(
        request.consumer_receipt.native_output_sha256,
        "consumer output hash",
    )
    _validate_producer(request.producer)


def extract_usage_evidence(request: UsageExtractionRequest) -> UsageEvidenceV1:
    """Rebuild and hash one literal route's exact typed producer references."""

    _validate_request_contract(request)
    if request.route_ordinal < 0 or request.route_ordinal >= len(USAGE_ROUTE_REGISTRY):
        _fail("usage route ordinal is outside the reviewed registry")
    route = USAGE_ROUTE_REGISTRY[request.route_ordinal]
    if (
        route.route_ordinal != request.route_ordinal
        or route.usage_kind != request.usage_kind
        or route.consumer_step_name != request.consumer_step_name
        or route.consumer_subrole != request.consumer_subrole
        or route.native_reference_relation != request.native_reference_relation
        or route.consumer_fk != request.consumer_fk
        or route.reference_kind != request.reference_kind
    ):
        _fail("usage request does not match its one literal route")

    extractor = USAGE_EXTRACTOR_REGISTRY.get(request.usage_kind)
    if extractor is None or route.route_ordinal not in extractor.accepted_route_ordinals:
        _fail("usage route is not admitted by its closed extractor")
    if not request.occurrences:
        _fail("usage extraction returned no typed producer occurrence")

    try:
        schema = CENSUS_SCHEMA_REGISTRY[route.census_schema_id]
    except KeyError as exc:
        raise UsageExtractorError(
            CONSUMER_RECEIPT_INCOMPLETE,
            "usage route names an unavailable CENSUS schema",
        ) from exc

    occurrences = tuple(sorted(request.occurrences, key=_occurrence_sort_key))
    if len({_occurrence_sort_key(row) for row in occurrences}) != len(occurrences):
        _fail("usage extraction contains duplicate reference occurrences")
    for row in occurrences:
        if (
            row.consumer_receipt_output_object_id != request.consumer_receipt.output_object_id
            or row.consumer_receipt_identity_sha256
            != request.consumer_receipt.native_identity_sha256
            or row.reference_schema_id != route.census_schema_id
            or row.reference_schema_version != schema.schema_version
            or row.reference_schema_sha256 != schema.schema_sha256
            or row.reference_path != route.json_pointer
            or not isinstance(row.reference_ordinal, int)
            or isinstance(row.reference_ordinal, bool)
            or row.reference_ordinal < 0
            or row.producer_ref != request.producer
        ):
            _fail("typed occurrence does not corroborate the exact route and producer")
        _validate_hash(row.consumer_receipt_identity_sha256, "occurrence consumer hash")
        _validate_hash(row.reference_schema_sha256, "occurrence schema hash")
        _validate_producer(row.producer_ref)

    usage_evidence_sha256 = _canonical_hash(
        [
            "consumer-usage-evidence-v1",
            str(request.tenant_id),
            str(request.deal_id),
            str(request.run_id),
            request.pipeline_contract_version,
            request.pipeline_contract_hash,
            str(request.expected_edge_id),
            request.edge_tuple_sha256,
            request.usage_kind,
            extractor.extractor_id,
            extractor.extractor_version,
            extractor.extractor_sha256,
            request.producer.as_contract_tuple(),
            str(request.consumer_receipt.step_output_id),
            str(request.consumer_receipt.output_object_id),
            request.consumer_receipt.native_identity_sha256,
            request.consumer_receipt.native_output_sha256,
            [row.as_contract_tuple() for row in occurrences],
        ]
    )
    return UsageEvidenceV1(
        expected_edge_id=request.expected_edge_id,
        route_ordinal=request.route_ordinal,
        usage_kind=request.usage_kind,
        extractor_id=extractor.extractor_id,
        extractor_version=extractor.extractor_version,
        extractor_sha256=extractor.extractor_sha256,
        occurrences=occurrences,
        usage_evidence_sha256=usage_evidence_sha256,
    )
