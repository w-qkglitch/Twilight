"""
Emby API

提供 Emby 相关查询操作
"""
from flask import Blueprint, request

from src.api.v1.auth import require_auth, api_response
from src.services import EmbyService, get_emby_client
from src.config import EmbyConfig

emby_bp = Blueprint('emby', __name__, url_prefix='/emby')


# ==================== 服务器信息 ====================

@emby_bp.route('/status', methods=['GET'])
@require_auth
async def get_server_status():
    """
    获取 Emby 服务器状态
    
    Response:
        {
            "success": true,
            "data": {
                "online": true,
                "server_name": "My Emby Server",
                "version": "4.7.0.0",
                "active_sessions": 5,
                "total_sessions": 8
            }
        }
    """
    status = await EmbyService.get_server_status()
    return api_response(status.get('online', False), 
                       status.get('message', 'OK') if not status.get('online') else 'Emby 在线',
                       status)


@emby_bp.route('/urls', methods=['GET'])
async def get_server_urls():
    """已弃用：请改用 /api/v1/system/emby-urls（按用户角色/绑定状态下发线路）。

    保留路径但拒绝服务，避免泄露线路给未鉴权访客。
    """
    return api_response(
        False,
        "该端点已弃用，请改用 /api/v1/system/emby-urls",
        code=410,
    )


# ==================== 媒体库 ====================

@emby_bp.route('/libraries', methods=['GET'])
@require_auth
async def get_libraries():
    """
    获取媒体库列表
    
    Response:
        {
            "success": true,
            "data": [
                {
                    "id": "xxx",
                    "name": "电影",
                    "type": "movies"
                }
            ]
        }
    """
    libraries = await EmbyService.get_libraries_info()
    return api_response(True, "获取成功", libraries)


# ==================== 媒体搜索 ====================

@emby_bp.route('/search', methods=['GET'])
@require_auth
async def search_media():
    """
    搜索媒体
    
    Query:
        q: str - 搜索关键词
        limit: int - 返回数量（默认 20，最大 50）
    
    Response:
        {
            "success": true,
            "data": [
                {
                    "id": "xxx",
                    "name": "电影名称",
                    "type": "Movie",
                    "year": 2023,
                    "overview": "简介..."
                }
            ]
        }
    """
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 20, type=int)
    
    if not query:
        return api_response(False, "缺少搜索关键词", code=400)
    
    limit = min(max(limit, 1), 50)
    results = await EmbyService.search_media(query, limit)
    
    return api_response(True, "搜索成功", results)


@emby_bp.route('/latest', methods=['GET'])
@require_auth
async def get_latest_media():
    """
    获取最新媒体
    
    Query:
        type: str - 媒体类型 (Movie, Series)
        limit: int - 返回数量（默认 20，最大 50）
    """
    item_type = request.args.get('type')
    limit = request.args.get('limit', 20, type=int)
    
    limit = min(max(limit, 1), 50)
    types = [item_type] if item_type else None
    
    results = await EmbyService.get_latest_media(types, limit)
    return api_response(True, "获取成功", results)


# ==================== 会话信息 ====================

@emby_bp.route('/sessions/count', methods=['GET'])
@require_auth
async def get_sessions_count():
    """获取当前活动会话数量"""
    emby = get_emby_client()
    
    try:
        sessions = await emby.get_sessions()
        active = len([s for s in sessions if s.is_active])
        
        return api_response(True, "获取成功", {
            'active_sessions': active,
            'total_sessions': len(sessions),
        })
    except Exception as e:
        return api_response(False, f"获取失败: {e}")

